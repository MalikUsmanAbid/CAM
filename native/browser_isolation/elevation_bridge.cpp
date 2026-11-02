#include "elevation_bridge.hpp"
#include <windows.h>
#include <shellapi.h>
#include <shlobj.h>
#include <sddl.h>
#include <aclapi.h>
#include <rpc.h>
#include <io.h>
#include <fcntl.h>
#include <thread>
#include <stdexcept>
#include <vector>

namespace jarvis {
namespace {
void require(bool value) { if (!value) throw std::runtime_error("Protected elevation bridge refused"); }
struct Handle { HANDLE value = nullptr; ~Handle() { if (value && value != INVALID_HANDLE_VALUE) CloseHandle(value); } };
std::wstring image(HANDLE process) {
  std::vector<wchar_t> buffer(32768); DWORD size = static_cast<DWORD>(buffer.size());
  require(QueryFullProcessImageNameW(process, 0, buffer.data(), &size)); return std::wstring(buffer.data(), size);
}
std::vector<BYTE> user(HANDLE process) {
  Handle token; require(OpenProcessToken(process, TOKEN_QUERY, &token.value)); DWORD size = 0;
  GetTokenInformation(token.value, TokenUser, nullptr, 0, &size);
  require(size > 0 && size < 65536); std::vector<BYTE> result(size);
  require(GetTokenInformation(token.value, TokenUser, result.data(), size, &size)); return result;
}
bool same_user(HANDLE other) {
  auto a = user(GetCurrentProcess()), b = user(other);
  return EqualSid(reinterpret_cast<TOKEN_USER*>(a.data())->User.Sid, reinterpret_cast<TOKEN_USER*>(b.data())->User.Sid);
}
bool trusted_sid(PSID sid) {
  PSID installer = nullptr;
  require(ConvertStringSidToSidW(L"S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464", &installer));
  bool result = IsWellKnownSid(sid, WinBuiltinAdministratorsSid) || IsWellKnownSid(sid, WinLocalSystemSid) || EqualSid(sid, installer);
  LocalFree(installer); return result;
}
void protected_path(const std::wstring& file) {
  PWSTR raw = nullptr; require(SUCCEEDED(SHGetKnownFolderPath(FOLDERID_ProgramFiles, 0, nullptr, &raw)));
  std::wstring root(raw); CoTaskMemFree(raw);
  require(file.size() > root.size() + 1 && !_wcsnicmp(file.c_str(), (root + L"\\").c_str(), root.size() + 1));
  // Reject writable/reparse components, not just a reassuring directory prefix.
  std::wstring path = root;
  size_t offset = root.size();
  for (;;) {
    DWORD attributes = GetFileAttributesW(path.c_str());
    require(attributes != INVALID_FILE_ATTRIBUTES && !(attributes & FILE_ATTRIBUTE_REPARSE_POINT));
    PSECURITY_DESCRIPTOR descriptor = nullptr; PSID owner = nullptr; PACL acl = nullptr;
    require(GetNamedSecurityInfoW(const_cast<wchar_t*>(path.c_str()), SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
      &owner, nullptr, &acl, nullptr, &descriptor) == ERROR_SUCCESS);
    bool safe = owner && trusted_sid(owner) && acl;
    if (acl) for (DWORD i = 0; i < acl->AceCount; ++i) {
      void* raw_ace = nullptr;
      if (!GetAce(acl, i, &raw_ace)) { safe = false; break; }
      auto header = static_cast<ACE_HEADER*>(raw_ace);
      if (header->AceFlags & INHERIT_ONLY_ACE) continue;
      if (header->AceType == ACCESS_DENIED_ACE_TYPE) continue;
      if (header->AceType != ACCESS_ALLOWED_ACE_TYPE) { safe = false; break; }
      auto ace = static_cast<ACCESS_ALLOWED_ACE*>(raw_ace);
      constexpr DWORD write = GENERIC_WRITE | GENERIC_ALL | FILE_WRITE_DATA | FILE_APPEND_DATA | FILE_WRITE_EA | FILE_WRITE_ATTRIBUTES | DELETE | WRITE_DAC | WRITE_OWNER | FILE_DELETE_CHILD;
      if ((ace->Mask & write) && !trusted_sid(&ace->SidStart)) safe = false;
    }
    LocalFree(descriptor); require(safe);
    if (path.size() == file.size()) break;
    offset = file.find(L'\\', offset + 1);
    if (offset == std::wstring::npos) offset = file.size();
    path = file.substr(0, offset);
  }
}
std::wstring pipe_name(const std::wstring& identity) {
  GUID id{}; require(identity.size() == 36 && UuidFromStringW(reinterpret_cast<RPC_WSTR>(const_cast<wchar_t*>(identity.c_str())), &id) == RPC_S_OK);
  return L"\\\\.\\pipe\\USMANJARVIS.Browser." + identity;
}
DWORD transfer(HANDLE pipe, void* data, DWORD size, bool write, bool overlapped) {
  Handle event; OVERLAPPED operation{};
  if (overlapped) { event.value = CreateEventW(nullptr, TRUE, FALSE, nullptr); require(event.value); operation.hEvent = event.value; }
  DWORD count = 0;
  BOOL ok = write ? WriteFile(pipe, data, size, &count, overlapped ? &operation : nullptr)
                  : ReadFile(pipe, data, size, &count, overlapped ? &operation : nullptr);
  if (!ok && overlapped && GetLastError() == ERROR_IO_PENDING) {
    if (WaitForSingleObject(event.value, 30000) != WAIT_OBJECT_0) {
      CancelIoEx(pipe, &operation); GetOverlappedResult(pipe, &operation, &count, TRUE); require(false);
    }
    ok = GetOverlappedResult(pipe, &operation, &count, FALSE);
  }
  require(ok && count); return count;
}
void exact(HANDLE pipe, void* data, DWORD size, bool write, bool overlapped) {
  auto bytes = static_cast<BYTE*>(data);
  for (DWORD offset = 0; offset < size;) offset += transfer(pipe, bytes + offset, size - offset, write, overlapped);
}
}

void require_protected_broker() { protected_path(image(GetCurrentProcess())); }

int request_managed_browser(const std::wstring& port, const std::wstring& browser) {
  try {
    const auto executable = image(GetCurrentProcess()); protected_path(executable);
    Handle requester_token; require(OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &requester_token.value));
    TOKEN_ELEVATION requester_elevation{}; DWORD elevation_bytes = 0;
    require(GetTokenInformation(requester_token.value, TokenElevation, &requester_elevation, sizeof(requester_elevation), &elevation_bytes)
      && !requester_elevation.TokenIsElevated); // Never normalize an elevated backend.
    Handle pin{CreateFileW(executable.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr)};
    require(pin.value != INVALID_HANDLE_VALUE);
    require(port.size() <= 5 && browser.size() <= 2000 && browser.find(L'\0') == std::wstring::npos);
    GUID id{}; require(UuidCreate(&id) == RPC_S_OK); RPC_WSTR raw = nullptr; require(UuidToStringW(&id, &raw) == RPC_S_OK);
    std::wstring identity(reinterpret_cast<wchar_t*>(raw)); RpcStringFreeW(&raw);
    auto token_user = user(GetCurrentProcess()); LPWSTR sid = nullptr;
    require(ConvertSidToStringSidW(reinterpret_cast<TOKEN_USER*>(token_user.data())->User.Sid, &sid));
    std::wstring sddl = L"D:P(A;;GA;;;" + std::wstring(sid) + L")(A;;GA;;;SY)"; LocalFree(sid);
    PSECURITY_DESCRIPTOR descriptor = nullptr;
    require(ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl.c_str(), SDDL_REVISION_1, &descriptor, nullptr));
    SECURITY_ATTRIBUTES security{sizeof(security), descriptor, FALSE};
    Handle pipe{CreateNamedPipeW(pipe_name(identity).c_str(), PIPE_ACCESS_DUPLEX | FILE_FLAG_FIRST_PIPE_INSTANCE | FILE_FLAG_OVERLAPPED,
      PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS, 1, 65536, 65536, 0, &security)};
    LocalFree(descriptor); require(pipe.value != INVALID_HANDLE_VALUE);
    Handle connected{CreateEventW(nullptr, TRUE, FALSE, nullptr)}; require(connected.value);
    OVERLAPPED connection{}; connection.hEvent = connected.value;
    BOOL initial = ConnectNamedPipe(pipe.value, &connection); DWORD connect_error = initial ? ERROR_SUCCESS : GetLastError();
    struct PendingConnection {
      HANDLE pipe; OVERLAPPED* operation; bool pending;
      ~PendingConnection() { if (pending) { CancelIoEx(pipe, operation); DWORD ignored; GetOverlappedResult(pipe, operation, &ignored, TRUE); } }
    } pending{pipe.value, &connection, !initial && connect_error == ERROR_IO_PENDING};
    require(initial || connect_error == ERROR_IO_PENDING || connect_error == ERROR_PIPE_CONNECTED);
    std::wstring parameters = L"--elevated-managed-pipe " + identity + L" " + std::to_wstring(GetCurrentProcessId());
    const std::wstring working_directory = executable.substr(0, executable.find_last_of(L'\\'));
    SHELLEXECUTEINFOW launch{sizeof(launch)}; launch.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC;
    launch.lpVerb = L"runas"; launch.lpFile = executable.c_str(); launch.lpParameters = parameters.c_str(); launch.lpDirectory = working_directory.c_str(); launch.nShow = SW_HIDE;
    if (!ShellExecuteExW(&launch)) { CancelIoEx(pipe.value, &connection); DWORD ignored; GetOverlappedResult(pipe.value, &connection, &ignored, TRUE); return 70; }
    Handle elevated{launch.hProcess}; require(elevated.value);
    if (connect_error == ERROR_IO_PENDING) {
      if (WaitForSingleObject(connected.value, 30000) != WAIT_OBJECT_0) {
        CancelIoEx(pipe.value, &connection); DWORD ignored; GetOverlappedResult(pipe.value, &connection, &ignored, TRUE); return 70;
      }
      DWORD count = 0; require(GetOverlappedResult(pipe.value, &connection, &count, FALSE));
      pending.pending = false;
    }
    ULONG client = 0; require(GetNamedPipeClientProcessId(pipe.value, &client) && client == GetProcessId(elevated.value) && same_user(elevated.value));
    const std::wstring contract = port + L"\n" + browser;
    DWORD size = static_cast<DWORD>(contract.size() * sizeof(wchar_t));
    exact(pipe.value, &size, sizeof(size), true, true);
    exact(pipe.value, const_cast<wchar_t*>(contract.data()), size, true, true);
    Handle stopped{CreateEventW(nullptr, TRUE, FALSE, nullptr)}; require(stopped.value);
    auto relay = [&](bool commands) {
      try {
        BYTE buffer[4096]; DWORD count = 0; size_t total = 0;
        for (;;) {
          if (commands) { if (!ReadFile(GetStdHandle(STD_INPUT_HANDLE), buffer, sizeof(buffer), &count, nullptr) || !count) break; }
          else count = transfer(pipe.value, buffer, sizeof(buffer), false, true);
          if ((total += count) > 32 * 1024 * 1024) break;
          if (commands) exact(pipe.value, buffer, count, true, true);
          else { DWORD offset = 0; while (offset < count) { DWORD written = 0; require(WriteFile(GetStdHandle(STD_OUTPUT_HANDLE), buffer + offset, count - offset, &written, nullptr) && written); offset += written; } }
        }
      } catch (...) {}
      SetEvent(stopped.value);
    };
    std::thread commands(relay, true), responses(relay, false);
    WaitForSingleObject(stopped.value, 600000);
    // Closing the connection tells the elevated owner to kill its browser job.
    DisconnectNamedPipe(pipe.value); CancelIoEx(pipe.value, nullptr);
    HANDLE threads[] = {commands.native_handle(), responses.native_handle()};
    bool joined = false;
    for (unsigned i = 0; i < 100; ++i) {
      CancelSynchronousIo(commands.native_handle()); CancelSynchronousIo(responses.native_handle());
      if (WaitForMultipleObjects(2, threads, TRUE, 20) == WAIT_OBJECT_0) { joined = true; break; }
    }
    if (!joined) ExitProcess(70);
    commands.join(); responses.join(); return 0;
  } catch (...) { return 70; }
}

std::vector<std::wstring> accept_managed_pipe(const std::wstring& identity, const std::wstring& launcher_pid) {
  const auto executable = image(GetCurrentProcess()); protected_path(executable);
  size_t used = 0; unsigned long requested = std::stoul(launcher_pid, &used);
  require(used == launcher_pid.size() && requested != 0);
  Handle launcher{OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, FALSE, requested)}; require(launcher.value);
  require(same_user(launcher.value) && !_wcsicmp(image(launcher.value).c_str(), executable.c_str()) && WaitForSingleObject(launcher.value, 0) == WAIT_TIMEOUT);
  Handle pipe{CreateFileW(pipe_name(identity).c_str(), GENERIC_READ | GENERIC_WRITE, 0, nullptr, OPEN_EXISTING,
    SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION, nullptr)};
  require(pipe.value != INVALID_HANDLE_VALUE);
  ULONG server = 0; require(GetNamedPipeServerProcessId(pipe.value, &server) && server == requested);
  DWORD bytes = 0; exact(pipe.value, &bytes, sizeof(bytes), false, false);
  require(bytes > 0 && bytes <= 4096 && bytes % sizeof(wchar_t) == 0);
  std::wstring contract(bytes / sizeof(wchar_t), L'\0'); exact(pipe.value, contract.data(), bytes, false, false);
  size_t split = contract.find(L'\n'); require(split != std::wstring::npos && split <= 5 && contract.find(L'\0') == std::wstring::npos);
  std::vector<std::wstring> arguments{executable, L"--managed-browser-cdp", contract.substr(0, split), contract.substr(split + 1)};
  // Duplicate private local pipe handles into CRT/std handles; never inherit a
  // network listener, console or caller-selected handle into Chromium.
  HANDLE input = nullptr, output = nullptr;
  require(DuplicateHandle(GetCurrentProcess(), pipe.value, GetCurrentProcess(), &input, 0, FALSE, DUPLICATE_SAME_ACCESS));
  require(DuplicateHandle(GetCurrentProcess(), pipe.value, GetCurrentProcess(), &output, 0, FALSE, DUPLICATE_SAME_ACCESS));
  int in_fd = _open_osfhandle(reinterpret_cast<intptr_t>(input), _O_BINARY);
  int out_fd = _open_osfhandle(reinterpret_cast<intptr_t>(output), _O_BINARY);
  require(in_fd >= 0 && out_fd >= 0);
  require(_dup2(in_fd, 0) == 0 && _dup2(out_fd, 1) == 0); _close(in_fd); _close(out_fd);
  require(SetStdHandle(STD_INPUT_HANDLE, reinterpret_cast<HANDLE>(_get_osfhandle(0))) && SetStdHandle(STD_OUTPUT_HANDLE, reinterpret_cast<HANDLE>(_get_osfhandle(1))));
  return arguments;
}
}
