#include "managed_deployment.hpp"
#include "elevation_bridge.hpp"
#include <sddl.h>
#include <aclapi.h>
#include <netfw.h>
#include <userenv.h>
#include <rpc.h>
#include <stdexcept>
#include <vector>

namespace jarvis {
namespace {
void require(bool value) { if (!value) throw std::runtime_error("Managed deployment refused; deny policy retained"); }
struct Key { HKEY value = nullptr; ~Key() { if (value) RegCloseKey(value); } };
struct Handle { HANDLE value = nullptr; ~Handle() { if (value) CloseHandle(value); } };
struct Descriptor {
  PSECURITY_DESCRIPTOR value = nullptr;
  Descriptor() { require(ConvertStringSecurityDescriptorToSecurityDescriptorW(L"O:BAG:BAD:P(A;;GA;;;SY)(A;;GA;;;BA)", SDDL_REVISION_1, &value, nullptr)); }
  ~Descriptor() { if (value) LocalFree(value); }
};
std::wstring text(const GUID& id) {
  RPC_WSTR value = nullptr;
  require(UuidToStringW(&id, &value) == RPC_S_OK);
  std::wstring result(reinterpret_cast<wchar_t*>(value)); RpcStringFreeW(&value); return result;
}
GUID parse(const std::wstring& value) {
  GUID id{}; require(value.size() == 36 && UuidFromStringW(reinterpret_cast<RPC_WSTR>(const_cast<wchar_t*>(value.c_str())), &id) == RPC_S_OK); return id;
}
std::wstring get(HKEY key, const wchar_t* name) {
  wchar_t buffer[513]{}; DWORD bytes = sizeof(buffer), type = 0;
  require(RegQueryValueExW(key, name, nullptr, &type, reinterpret_cast<BYTE*>(buffer), &bytes) == ERROR_SUCCESS
    && type == REG_SZ && bytes >= sizeof(wchar_t) && bytes <= sizeof(buffer) && bytes % sizeof(wchar_t) == 0
    && buffer[bytes / sizeof(wchar_t) - 1] == L'\0');
  std::wstring result(buffer); require((result.size() + 1) * sizeof(wchar_t) == bytes); return result;
}
void put(HKEY key, const wchar_t* name, const std::wstring& value) {
  require(value.size() <= 512 && RegSetValueExW(key, name, 0, REG_SZ, reinterpret_cast<const BYTE*>(value.c_str()), static_cast<DWORD>((value.size() + 1) * sizeof(wchar_t))) == ERROR_SUCCESS);
}
void elevated_interactive_user() {
  Handle token; require(OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token.value));
  TOKEN_ELEVATION elevation{}; DWORD bytes = 0, session = 0;
  require(GetTokenInformation(token.value, TokenElevation, &elevation, sizeof(elevation), &bytes) && elevation.TokenIsElevated);
  require(GetTokenInformation(token.value, TokenSessionId, &session, sizeof(session), &bytes) && session != 0);
  GetTokenInformation(token.value, TokenUser, nullptr, 0, &bytes);
  std::vector<BYTE> user(bytes); require(bytes && GetTokenInformation(token.value, TokenUser, user.data(), bytes, &bytes));
  auto sid = reinterpret_cast<TOKEN_USER*>(user.data())->User.Sid;
  require(!IsWellKnownSid(sid, WinLocalSystemSid) && !IsWellKnownSid(sid, WinLocalServiceSid) && !IsWellKnownSid(sid, WinNetworkServiceSid));
}
std::wstring current_user_sid() {
  Handle token; require(OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token.value)); DWORD bytes = 0;
  GetTokenInformation(token.value, TokenUser, nullptr, 0, &bytes);
  require(bytes > 0 && bytes < 65536); std::vector<BYTE> user(bytes);
  require(GetTokenInformation(token.value, TokenUser, user.data(), bytes, &bytes));
  LPWSTR sid = nullptr; require(ConvertSidToStringSidW(reinterpret_cast<TOKEN_USER*>(user.data())->User.Sid, &sid));
  std::wstring result(sid); LocalFree(sid); return result;
}
void secure_key(HKEY key) {
  PSECURITY_DESCRIPTOR descriptor = nullptr; PACL acl = nullptr; PSID owner = nullptr;
  require(GetSecurityInfo(key, SE_REGISTRY_KEY, OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION, &owner, nullptr, &acl, nullptr, &descriptor) == ERROR_SUCCESS);
  bool safe = owner && (IsWellKnownSid(owner, WinBuiltinAdministratorsSid) || IsWellKnownSid(owner, WinLocalSystemSid)) && acl;
  SECURITY_DESCRIPTOR_CONTROL control{}; DWORD revision = 0;
  safe = safe && GetSecurityDescriptorControl(descriptor, &control, &revision) && (control & SE_DACL_PROTECTED);
  if (acl) for (DWORD index = 0; index < acl->AceCount; ++index) {
    void* raw = nullptr; if (!GetAce(acl, index, &raw)) { safe = false; break; }
    auto header = static_cast<ACE_HEADER*>(raw);
    if (header->AceType != ACCESS_ALLOWED_ACE_TYPE) { safe = false; break; }
    auto ace = static_cast<ACCESS_ALLOWED_ACE*>(raw);
    PSID sid = &ace->SidStart;
    if (!IsWellKnownSid(sid, WinBuiltinAdministratorsSid) && !IsWellKnownSid(sid, WinLocalSystemSid)) safe = false;
  }
  LocalFree(descriptor); require(safe);
}
void make_key(HKEY parent, const wchar_t* path, DWORD options, HKEY* key) {
  Descriptor descriptor; SECURITY_ATTRIBUTES security{sizeof(security), descriptor.value, FALSE};
  require(RegCreateKeyExW(parent, path, 0, nullptr, options, KEY_ALL_ACCESS | KEY_WOW64_64KEY, &security, key, nullptr) == ERROR_SUCCESS);
  secure_key(*key);
}
bool loopback_present(PSID wanted) {
  DWORD count = 0; PSID_AND_ATTRIBUTES entries = nullptr;
  // This API has no standalone SDK import library. Resolve the OS DLL only
  // from System32; an application-directory DLL must never supply policy data.
  struct Module { HMODULE value; ~Module() { if (value) FreeLibrary(value); } };
  Module module{LoadLibraryExW(L"FirewallAPI.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32)};
  require(module.value != nullptr);
  auto query = reinterpret_cast<decltype(&NetworkIsolationGetAppContainerConfig)>(
    GetProcAddress(module.value, "NetworkIsolationGetAppContainerConfig"));
  require(query != nullptr);
  require(query(&count, &entries) == ERROR_SUCCESS);
  bool found = false;
  for (DWORD i = 0; i < count; ++i) {
    if (entries[i].Sid && EqualSid(entries[i].Sid, wanted)) found = true;
    HeapFree(GetProcessHeap(), 0, entries[i].Sid);
  }
  HeapFree(GetProcessHeap(), 0, entries); return found;
}
void loopback(const std::wstring& sid_text, bool enable) {
  PSID sid = nullptr; require(ConvertStringSidToSidW(sid_text.c_str(), &sid));
  struct SidOwner { PSID value; ~SidOwner() { LocalFree(value); } } owner{sid};
  if (loopback_present(sid) == enable) return;
  wchar_t system[MAX_PATH]{}, windows[MAX_PATH]{};
  UINT system_length = GetSystemDirectoryW(system, MAX_PATH), windows_length = GetWindowsDirectoryW(windows, MAX_PATH);
  require(system_length > 0 && system_length < MAX_PATH && windows_length > 0 && windows_length < MAX_PATH);
  const std::wstring program = std::wstring(system) + L"\\CheckNetIsolation.exe";
  // Single-SID OS operation; never replace a snapshot of other applications.
  std::wstring command = L"\"" + program + L"\" LoopbackExempt " + (enable ? L"-a" : L"-d") + L" -p=" + sid_text;
  Handle job; job.value = CreateJobObjectW(nullptr, nullptr); require(job.value);
  JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
  limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_ACTIVE_PROCESS;
  limits.BasicLimitInformation.ActiveProcessLimit = 1;
  require(SetInformationJobObject(job.value, JobObjectExtendedLimitInformation, &limits, sizeof(limits)));
  STARTUPINFOW startup{sizeof(startup)}; PROCESS_INFORMATION process{};
  std::wstring environment = L"SystemRoot=" + std::wstring(windows);
  environment.push_back(L'\0'); environment.push_back(L'\0');
  require(CreateProcessW(program.c_str(), command.data(), nullptr, nullptr, FALSE, CREATE_SUSPENDED | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT,
    environment.data(), system, &startup, &process));
  Handle child{process.hProcess}, thread{process.hThread};
  if (!AssignProcessToJobObject(job.value, child.value)) { TerminateProcess(child.value, 70); require(false); }
  require(ResumeThread(thread.value) != static_cast<DWORD>(-1));
  require(WaitForSingleObject(child.value, 10000) == WAIT_OBJECT_0);
  DWORD code = 70; require(GetExitCodeProcess(child.value, &code) && code == 0);
  require(loopback_present(sid) == enable);
}

void recover_record(HKEY key) {
  const auto profile = get(key, L"profile"), sid_text = get(key, L"sid");
  const std::wstring prefix = L"USMANJARVIS.Browser.";
  require(profile.rfind(prefix, 0) == 0); parse(profile.substr(prefix.size()));
  PSID sid = nullptr, derived = nullptr;
  require(ConvertStringSidToSidW(sid_text.c_str(), &sid));
  struct Sids { PSID a; PSID b = nullptr; ~Sids() { if (a) LocalFree(a); if (b) FreeSid(b); } } sids{sid};
  require(SUCCEEDED(DeriveAppContainerSidFromAppContainerName(profile.c_str(), &derived))); sids.b = derived;
  require(EqualSid(sid, derived));
  GUID sublayer = parse(get(key, L"sublayer"));
  std::vector<GUID> filters;
  for (unsigned i = 0; i < 6; ++i) filters.push_back(parse(get(key, (L"filter" + std::to_wstring(i)).c_str())));
  // Revoke the exception BEFORE any deny filter is removed.
  loopback(sid_text, false);
  HANDLE engine = nullptr; require(FwpmEngineOpen0(nullptr, RPC_C_AUTHN_WINNT, nullptr, nullptr, &engine) == ERROR_SUCCESS);
  struct Engine { HANDLE value; bool transaction = false; ~Engine() { if (transaction) FwpmTransactionAbort0(value); FwpmEngineClose0(value); } } owner{engine};
  require(FwpmTransactionBegin0(engine, 0) == ERROR_SUCCESS); owner.transaction = true;
  for (const auto& id : filters) {
    FWPM_FILTER0* filter = nullptr;
    DWORD status = FwpmFilterGetByKey0(engine, &id, &filter);
    if (status == FWP_E_FILTER_NOT_FOUND) continue; // Interrupted cleanup is idempotent.
    require(status == ERROR_SUCCESS);
    bool scoped = false;
    bool safe = filter->subLayerKey == sublayer && filter->action.type == FWP_ACTION_BLOCK && (filter->flags & FWPM_FILTER_FLAG_PERSISTENT);
    for (UINT32 i = 0; i < filter->numFilterConditions && i < 32; ++i) {
      const auto& c = filter->filterCondition[i];
      if (c.fieldKey == FWPM_CONDITION_ALE_PACKAGE_ID && c.matchType == FWP_MATCH_EQUAL && c.conditionValue.type == FWP_SID
          && c.conditionValue.sid && EqualSid(c.conditionValue.sid, sid)) scoped = true;
    }
    FwpmFreeMemory0(reinterpret_cast<void**>(&filter)); require(safe && scoped);
    require(FwpmFilterDeleteByKey0(engine, &id) == ERROR_SUCCESS);
  }
  DWORD deleted = FwpmSubLayerDeleteByKey0(engine, &sublayer);
  require(deleted == ERROR_SUCCESS || deleted == FWP_E_SUBLAYER_NOT_FOUND);
  require(FwpmTransactionCommit0(engine) == ERROR_SUCCESS); owner.transaction = false;
  HRESULT removed = DeleteAppContainerProfile(profile.c_str());
  require(SUCCEEDED(removed) || removed == HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND));
}
}

ManagedDeployment::ManagedDeployment() {
  require_protected_broker();
  elevated_interactive_user();
  actor_ = current_user_sid();
  try {
    Descriptor descriptor; SECURITY_ATTRIBUTES security{sizeof(security), descriptor.value, FALSE};
    mutex_ = CreateMutexW(&security, FALSE, L"Global\\USMANJARVIS.NativeBrowserDeployment.v1"); require(mutex_);
    DWORD acquired = WaitForSingleObject(mutex_, 0);
    require(acquired == WAIT_OBJECT_0 || acquired == WAIT_ABANDONED);
    make_key(HKEY_LOCAL_MACHINE, L"SOFTWARE\\USMANJARVIS\\NativeBrowserRecoveryV1", REG_OPTION_NON_VOLATILE, &records_);
    Key epoch; make_key(records_, L"BootEpoch", REG_OPTION_VOLATILE, &epoch.value);
    wchar_t buffer[40]{}; DWORD bytes = sizeof(buffer);
    if (RegGetValueW(epoch.value, nullptr, L"value", RRF_RT_REG_SZ, nullptr, buffer, &bytes) == ERROR_FILE_NOT_FOUND) {
      GUID identity{}; require(UuidCreate(&identity) == RPC_S_OK); put(epoch.value, L"value", text(identity));
    }
    epoch_ = get(epoch.value, L"value"); parse(epoch_);
    recover_previous_boot();
  } catch (...) {
    if (records_) { RegCloseKey(records_); records_ = nullptr; }
    if (mutex_) { ReleaseMutex(mutex_); CloseHandle(mutex_); mutex_ = nullptr; }
    throw;
  }
}
void ManagedDeployment::recover_previous_boot() {
  std::vector<std::wstring> names;
  for (DWORD i = 0; ; ++i) {
    wchar_t name[128]{}; DWORD length = 128;
    LONG status = RegEnumKeyExW(records_, i, name, &length, nullptr, nullptr, nullptr, nullptr);
    if (status == ERROR_NO_MORE_ITEMS) break;
    require(status == ERROR_SUCCESS && i < 128);
    if (std::wstring(name) != L"BootEpoch") names.emplace_back(name);
  }
  for (const auto& name : names) {
    parse(name); Key record;
    require(RegOpenKeyExW(records_, name.c_str(), 0, KEY_ALL_ACCESS | KEY_WOW64_64KEY, &record.value) == ERROR_SUCCESS);
    secure_key(record.value);
    wchar_t state[32]{}; DWORD bytes = sizeof(state);
    LONG state_status = RegGetValueW(record.value, nullptr, L"state", RRF_RT_REG_SZ, nullptr, state, &bytes);
    // Incomplete records cannot have committed a fence or enabled loopback.
    // Retain them for diagnosis without blocking all future managed sessions.
    if (state_status == ERROR_FILE_NOT_FOUND) continue;
    require(state_status == ERROR_SUCCESS && std::wstring(state) == L"prepared-v1");
    if (get(record.value, L"actor") != actor_) continue; // Never recover another user's profile.
    if (get(record.value, L"epoch") == epoch_) continue; // No same-boot process-snapshot guess.
    recover_record(record.value);
    RegCloseKey(record.value); record.value = nullptr;
    require(RegDeleteKeyExW(records_, name.c_str(), KEY_WOW64_64KEY, 0) == ERROR_SUCCESS);
  }
}
void ManagedDeployment::prepare(const BrowserFence& fence) {
  require(record_.empty() && fence.filters().size() == 6);
  record_ = text(fence.sublayer()); Key record;
  make_key(records_, record_.c_str(), REG_OPTION_NON_VOLATILE, &record.value);
  put(record.value, L"profile", fence.profile()); put(record.value, L"sid", fence.sid_string());
  put(record.value, L"sublayer", record_); put(record.value, L"epoch", epoch_);
  put(record.value, L"actor", actor_);
  for (unsigned i = 0; i < 6; ++i) put(record.value, (L"filter" + std::to_wstring(i)).c_str(), text(fence.filters()[i]));
  put(record.value, L"state", L"prepared-v1");
  require(RegFlushKey(record.value) == ERROR_SUCCESS); // Durable identity before a grant.
}
void ManagedDeployment::enable_loopback(const BrowserFence& fence) {
  require(record_ == text(fence.sublayer()));
  loopback(fence.sid_string(), true);
}
bool ManagedDeployment::finish(BrowserFence& fence, HANDLE job) {
  JOBOBJECT_BASIC_ACCOUNTING_INFORMATION accounting{};
  if (!job || !QueryInformationJobObject(job, JobObjectBasicAccountingInformation, &accounting, sizeof(accounting), nullptr) || accounting.ActiveProcesses) return false;
  try {
    loopback(fence.sid_string(), false);
    if (!fence.remove_after_job_empty(job)) return false;
    require(RegDeleteKeyExW(records_, record_.c_str(), KEY_WOW64_64KEY, 0) == ERROR_SUCCESS);
    record_.clear(); return true;
  } catch (...) { return false; }
}
ManagedDeployment::~ManagedDeployment() {
  // Exceptions/crashes retain the journal/deny, never grant a weaker fallback.
  if (records_) RegCloseKey(records_);
  if (mutex_) { ReleaseMutex(mutex_); CloseHandle(mutex_); }
}
}
