#include <windows.h>
#include <initguid.h>
#include "browser_fence.hpp"
#include <userenv.h>
#include <sddl.h>
#include <rpc.h>
#include <stdexcept>

namespace jarvis {
static void check(DWORD result) {
  if (result != ERROR_SUCCESS) throw std::runtime_error("Native isolation operation refused");
}
static GUID guid() {
  GUID result{};
  const auto status = UuidCreate(&result);
  if (status != RPC_S_OK && status != RPC_S_UUID_LOCAL_ONLY) throw std::runtime_error("Isolation identity unavailable");
  return result;
}
static FWPM_FILTER_CONDITION0 package(PSID sid) {
  FWPM_FILTER_CONDITION0 result{};
  result.fieldKey = FWPM_CONDITION_ALE_PACKAGE_ID;
  result.matchType = FWP_MATCH_EQUAL;
  result.conditionValue.type = FWP_SID;
  result.conditionValue.sid = reinterpret_cast<SID*>(sid);
  return result;
}

BrowserFence::BrowserFence(unsigned short proxy_port, std::function<void(const BrowserFence&)> journal) {
  if (proxy_port < 1024) throw std::runtime_error("Explicit nonprivileged proxy port required");
  const GUID identity = guid();
  RPC_WSTR text = nullptr;
  check(UuidToStringW(&identity, &text));
  profile_ = L"USMANJARVIS.Browser." + std::wstring(reinterpret_cast<wchar_t*>(text));
  RpcStringFreeW(&text);
  // Never reuse a pre-existing profile with unknown grants/capabilities.
  HRESULT created = CreateAppContainerProfile(profile_.c_str(), L"JARVIS isolated browser", L"Disposable verification profile", nullptr, 0, &sid_);
  if (FAILED(created)) throw std::runtime_error("Fresh AppContainer profile unavailable");
  bool transaction = false;
  try {
    check(FwpmEngineOpen0(nullptr, RPC_C_AUTHN_WINNT, nullptr, nullptr, &engine_));
    check(FwpmTransactionBegin0(engine_, 0));
    transaction = true;
    sublayer_ = guid();
    FWPM_SUBLAYER0 sublayer{};
    sublayer.subLayerKey = sublayer_;
    sublayer.displayData.name = const_cast<wchar_t*>(L"JARVIS package network fence");
    sublayer.flags = FWPM_SUBLAYER_FLAG_PERSISTENT;
    sublayer.weight = 0x7fff;
    check(FwpmSubLayerAdd0(engine_, &sublayer, nullptr));
    const auto scoped = package(sid_);
    // IPv6 is wholly denied, including mapped/direct/proxy-bypass paths.
    add(FWPM_LAYER_ALE_AUTH_CONNECT_V6, {scoped});
    add(FWPM_LAYER_ALE_AUTH_RECV_ACCEPT_V4, {scoped});
    add(FWPM_LAYER_ALE_AUTH_RECV_ACCEPT_V6, {scoped});
    // IPv4 connect is possible only to TCP 127.0.0.1:<owned proxy port>.
    // Separate deny filters implement OR; conditions within each are AND.
    FWPM_FILTER_CONDITION0 address{};
    address.fieldKey = FWPM_CONDITION_IP_REMOTE_ADDRESS;
    address.matchType = FWP_MATCH_NOT_EQUAL;
    address.conditionValue.type = FWP_UINT32;
    address.conditionValue.uint32 = 0x7f000001; // Host-order IPv4 value.
    add(FWPM_LAYER_ALE_AUTH_CONNECT_V4, {scoped, address});
    FWPM_FILTER_CONDITION0 protocol{};
    protocol.fieldKey = FWPM_CONDITION_IP_PROTOCOL;
    protocol.matchType = FWP_MATCH_NOT_EQUAL;
    protocol.conditionValue.type = FWP_UINT8;
    protocol.conditionValue.uint8 = 6;
    add(FWPM_LAYER_ALE_AUTH_CONNECT_V4, {scoped, protocol});
    FWPM_FILTER_CONDITION0 port{};
    port.fieldKey = FWPM_CONDITION_IP_REMOTE_PORT;
    port.matchType = FWP_MATCH_NOT_EQUAL;
    port.conditionValue.type = FWP_UINT16;
    port.conditionValue.uint16 = proxy_port;
    add(FWPM_LAYER_ALE_AUTH_CONNECT_V4, {scoped, port});
    // Persist ownership BEFORE committing policy; a crash cannot leave an
    // unrecorded managed fence. No loopback grant is made by this callback.
    if (journal) journal(*this);
    check(FwpmTransactionCommit0(engine_));
    transaction = false;
    installed_ = true;
  } catch (...) {
    if (transaction) FwpmTransactionAbort0(engine_);
    if (engine_) FwpmEngineClose0(engine_);
    engine_ = nullptr;
    DeleteAppContainerProfile(profile_.c_str());
    FreeSid(sid_); sid_ = nullptr;
    throw;
  }
}

void BrowserFence::add(const GUID& layer, const std::vector<FWPM_FILTER_CONDITION0>& conditions) {
  FWPM_FILTER0 filter{};
  filter.filterKey = guid();
  filter.displayData.name = const_cast<wchar_t*>(L"JARVIS package-only persistent deny");
  filter.layerKey = layer;
  filter.subLayerKey = sublayer_;
  filter.flags = FWPM_FILTER_FLAG_PERSISTENT;
  filter.action.type = FWP_ACTION_BLOCK;
  filter.weight.type = FWP_EMPTY;
  filter.numFilterConditions = static_cast<UINT32>(conditions.size());
  filter.filterCondition = const_cast<FWPM_FILTER_CONDITION0*>(conditions.data());
  check(FwpmFilterAdd0(engine_, &filter, nullptr, nullptr));
  filters_.push_back(filter.filterKey);
}

std::wstring BrowserFence::sid_string() const {
  LPWSTR text = nullptr;
  if (!ConvertSidToStringSidW(sid_, &text)) throw std::runtime_error("Package identity unavailable");
  std::wstring result(text);
  LocalFree(text);
  return result;
}

bool BrowserFence::remove_after_job_empty(HANDLE job) {
  JOBOBJECT_BASIC_ACCOUNTING_INFORMATION accounting{};
  if (!job || !QueryInformationJobObject(job, JobObjectBasicAccountingInformation, &accounting, sizeof(accounting), nullptr)
      || accounting.ActiveProcesses != 0) return false;
  if (!installed_) {
    HRESULT removed = DeleteAppContainerProfile(profile_.c_str());
    return SUCCEEDED(removed) || removed == HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND);
  }
  if (FwpmTransactionBegin0(engine_, 0) != ERROR_SUCCESS) return false;
  for (const auto& key : filters_) {
    if (FwpmFilterDeleteByKey0(engine_, &key) != ERROR_SUCCESS) { FwpmTransactionAbort0(engine_); return false; }
  }
  if (FwpmSubLayerDeleteByKey0(engine_, &sublayer_) != ERROR_SUCCESS || FwpmTransactionCommit0(engine_) != ERROR_SUCCESS) {
    FwpmTransactionAbort0(engine_); return false;
  }
  installed_ = false;
  HRESULT removed = DeleteAppContainerProfile(profile_.c_str());
  return SUCCEEDED(removed) || removed == HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND);
}

BrowserFence::~BrowserFence() {
  // Never remove policy merely because the controller exits or throws.
  if (engine_) FwpmEngineClose0(engine_);
  if (sid_) FreeSid(sid_);
}
}
