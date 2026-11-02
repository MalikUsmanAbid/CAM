// Read-only recovery evidence. Names identify candidates, never deletion authority.
#include <windows.h>
#include <fwpmu.h>
#include <sddl.h>
#include <rpc.h>
#include <iostream>
#include <string>

static std::wstring guid_text(const GUID& key) {
  RPC_WSTR text = nullptr;
  if (UuidToStringW(&key, &text) != RPC_S_OK) return L"unavailable";
  std::wstring result(reinterpret_cast<wchar_t*>(text));
  RpcStringFreeW(&text);
  return result;
}

int wmain(int argc, wchar_t** argv) {
  if (argc != 2 || std::wstring(argv[1]) != L"--inventory") return 64;
  HANDLE engine = nullptr, enumeration = nullptr;
  if (FwpmEngineOpen0(nullptr, RPC_C_AUTHN_WINNT, nullptr, nullptr, &engine) != ERROR_SUCCESS) return 70;
  DWORD status = FwpmFilterCreateEnumHandle0(engine, nullptr, &enumeration);
  unsigned total = 0, candidates = 0;
  if (status == ERROR_SUCCESS) {
    for (;;) {
      FWPM_FILTER0** entries = nullptr;
      UINT32 count = 0;
      status = FwpmFilterEnum0(engine, enumeration, 64, &entries, &count);
      if (status != ERROR_SUCCESS) { if (entries) FwpmFreeMemory0(reinterpret_cast<void**>(&entries)); break; }
      bool oversized = false;
      for (UINT32 i = 0; i < count; ++i) {
        if (++total > 65536) { oversized = true; break; }
        const auto& filter = *entries[i];
        if (!filter.displayData.name || std::wstring(filter.displayData.name) != L"JARVIS package-only persistent deny") continue;
        ++candidates;
        std::wcout << L"candidate filter=" << guid_text(filter.filterKey) << L" sublayer=" << guid_text(filter.subLayerKey)
                   << L" block=" << (filter.action.type == FWP_ACTION_BLOCK ? 1 : 0)
                   << L" persistent=" << ((filter.flags & FWPM_FILTER_FLAG_PERSISTENT) ? 1 : 0);
        for (UINT32 j = 0; j < filter.numFilterConditions && j < 32; ++j) {
          const auto& condition = filter.filterCondition[j];
          if (condition.fieldKey == FWPM_CONDITION_ALE_PACKAGE_ID && condition.conditionValue.type == FWP_SID
              && condition.conditionValue.sid && IsValidSid(condition.conditionValue.sid)) {
            LPWSTR sid = nullptr;
            if (ConvertSidToStringSidW(condition.conditionValue.sid, &sid)) { std::wcout << L" package=" << sid; LocalFree(sid); }
          }
        }
        std::wcout << L"\n";
      }
      if (entries) FwpmFreeMemory0(reinterpret_cast<void**>(&entries));
      if (oversized) { status = ERROR_MORE_DATA; break; }
      if (count < 64) break;
    }
    FwpmFilterDestroyEnumHandle0(engine, enumeration);
  }
  FwpmEngineClose0(engine);
  if (status != ERROR_SUCCESS) { std::wcerr << L"Inventory incomplete; no recovery authorization.\n"; return 70; }
  std::wcout << L"candidates=" << candidates << L"; visible objects only; no filters/profiles/exemptions changed.\n";
  return 0;
}
