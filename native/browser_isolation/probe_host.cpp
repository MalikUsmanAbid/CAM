// Disposable acceptance modes plus gated managed deployment/CDP modes.
// Managed mode uses explicit UAC and exact-package loopback; never a service.
#include "browser_fence.hpp"
#include "managed_deployment.hpp"
#include "elevation_bridge.hpp"
#include <sddl.h>
#include <userenv.h>
#include <thread>
#include <iostream>
#include <stdexcept>
#include <vector>
#include <memory>

struct Handle {
  HANDLE value = nullptr;
  ~Handle() { if (value && value != INVALID_HANDLE_VALUE) CloseHandle(value); }
};
static std::wstring quote(const std::wstring& value) {
  std::wstring output = L"\"";
  size_t slashes = 0;
  for (wchar_t character : value) {
    if (character == L'\\') { ++slashes; continue; }
    if (character == L'\"') output.append(slashes * 2 + 1, L'\\');
    else output.append(slashes, L'\\');
    slashes = 0; output += character;
  }
  output.append(slashes * 2, L'\\');
  return output + L"\"";
}
int wmain(int argc, wchar_t** argv) {
  std::vector<std::wstring> managed_arguments;
  std::vector<wchar_t*> managed_argv;
  if (argc == 4 && std::wstring(argv[1]) == L"--request-managed-browser")
    return jarvis::request_managed_browser(argv[2], argv[3]);
  if (argc == 4 && std::wstring(argv[1]) == L"--elevated-managed-pipe") {
    try {
      managed_arguments = jarvis::accept_managed_pipe(argv[2], argv[3]);
      for (auto& argument : managed_arguments) managed_argv.push_back(argument.data());
      argc = static_cast<int>(managed_argv.size()); argv = managed_argv.data();
    } catch (...) { return 70; }
  }
  if (argc == 2 && std::wstring(argv[1]) == L"--recover-managed-browser") {
    try { jarvis::ManagedDeployment recovery; return 0; } catch (...) { return 70; }
  }
  if (argc < 4) return 64;
  const bool managed_mode = std::wstring(argv[1]) == L"--managed-browser-cdp";
  const bool browser_mode = managed_mode || std::wstring(argv[1]) == L"--isolated-browser-cdp";
  if (!browser_mode && std::wstring(argv[1]) != L"--disposable-network-probe") return 64;
  if (browser_mode && argc != 4) return 64;
  try {
    size_t consumed = 0;
    unsigned long port = std::stoul(argv[2], &consumed);
    if (consumed != wcslen(argv[2]) || port < 1024 || port > 65535) return 64;
    std::wstring executable(argv[3]);
    if (executable.size() < 7 || executable[1] != L':' || executable[2] != L'\\' || executable.substr(executable.size() - 4) != L".exe") return 64;
    // Persistent package-only policy is committed before any child exists.
    std::unique_ptr<jarvis::ManagedDeployment> deployment;
    if (managed_mode) deployment = std::make_unique<jarvis::ManagedDeployment>();
    jarvis::BrowserFence fence(static_cast<unsigned short>(port), [&](const jarvis::BrowserFence& prepared) {
      if (deployment) deployment->prepare(prepared);
    });
    if (deployment) deployment->enable_loopback(fence);
    Handle job;
    job.value = CreateJobObjectW(nullptr, nullptr);
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_ACTIVE_PROCESS;
    limits.BasicLimitInformation.ActiveProcessLimit = 16;
    if (browser_mode) {
      limits.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_JOB_MEMORY | JOB_OBJECT_LIMIT_PROCESS_MEMORY;
      limits.JobMemoryLimit = 768ULL * 1024 * 1024;
      limits.ProcessMemoryLimit = 384ULL * 1024 * 1024;
    }
    if (!job.value || !SetInformationJobObject(job.value, JobObjectExtendedLimitInformation, &limits, sizeof(limits))) return 70;
    SECURITY_CAPABILITIES security{};
    security.AppContainerSid = fence.package_sid(); // No internet/private-network capabilities.
    SIZE_T bytes = 0;
    const DWORD attribute_count = browser_mode ? 2 : 1;
    InitializeProcThreadAttributeList(nullptr, attribute_count, 0, &bytes);
    std::vector<unsigned char> attributes(bytes);
    auto list = reinterpret_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(attributes.data());
    if (!InitializeProcThreadAttributeList(list, attribute_count, 0, &bytes)) return 70;
    struct AttributeOwner { LPPROC_THREAD_ATTRIBUTE_LIST list; ~AttributeOwner() { DeleteProcThreadAttributeList(list); } } owner{list};
    if (!UpdateProcThreadAttribute(list, 0, PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES, &security, sizeof(security), nullptr, nullptr)) return 70;
    STARTUPINFOEXW startup{};
    startup.StartupInfo.cb = sizeof(startup);
    startup.lpAttributeList = list;
    std::wstring command = quote(executable);
    Handle browser_input, browser_output, parent_input, parent_output;
    HANDLE inherited[2]{};
    if (browser_mode) {
      SECURITY_ATTRIBUTES pipe_security{sizeof(SECURITY_ATTRIBUTES), nullptr, TRUE};
      if (!CreatePipe(&browser_input.value, &parent_input.value, &pipe_security, 65536)
          || !CreatePipe(&parent_output.value, &browser_output.value, &pipe_security, 65536)
          || !SetHandleInformation(parent_input.value, HANDLE_FLAG_INHERIT, 0)
          || !SetHandleInformation(parent_output.value, HANDLE_FLAG_INHERIT, 0)) return 70;
      inherited[0] = browser_input.value; inherited[1] = browser_output.value;
      if (!UpdateProcThreadAttribute(list, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST, inherited, sizeof(inherited), nullptr, nullptr)) return 70;
      PWSTR storage = nullptr;
      if (FAILED(GetAppContainerFolderPath(fence.sid_string().c_str(), &storage))) return 70;
      const std::wstring profile_directory = std::wstring(storage) + L"\\Browser";
      CoTaskMemFree(storage);
      if (!CreateDirectoryW(profile_directory.c_str(), nullptr)) return 70;
      command += L" --remote-debugging-pipe --remote-debugging-io-pipes="
          + std::to_wstring(reinterpret_cast<ULONG_PTR>(browser_input.value)) + L"," + std::to_wstring(reinterpret_cast<ULONG_PTR>(browser_output.value));
      command += L" --user-data-dir=" + quote(profile_directory)
          + L" --proxy-server=http://127.0.0.1:" + std::to_wstring(port)
          + L" --proxy-bypass-list=\"<-loopback>\" --disable-quic --disable-background-networking --disable-extensions --no-first-run about:blank";
    } else {
      for (int index = 4; index < argc; ++index) command += L" " + quote(argv[index]);
    }
    wchar_t windows[MAX_PATH]{};
    if (!GetWindowsDirectoryW(windows, MAX_PATH)) return 70;
    std::wstring environment = L"SystemRoot=" + std::wstring(windows);
    environment.push_back(L'\0'); environment.push_back(L'\0');
    // The policy controller may require administrative WFP rights; its child
    // must not inherit an administrator token. No unrestricted launch fallback.
    Handle controller_token, restricted_token;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_DUPLICATE | TOKEN_ASSIGN_PRIMARY | TOKEN_QUERY | TOKEN_ADJUST_DEFAULT, &controller_token.value)
        || !CreateRestrictedToken(controller_token.value, DISABLE_MAX_PRIVILEGE | LUA_TOKEN, 0, nullptr, 0, nullptr, 0, nullptr, &restricted_token.value)) return 70;
    PSID medium = nullptr;
    if (!ConvertStringSidToSidW(L"S-1-16-8192", &medium)) return 70;
    TOKEN_MANDATORY_LABEL mandatory{};
    mandatory.Label.Sid = medium;
    mandatory.Label.Attributes = SE_GROUP_INTEGRITY;
    BOOL assigned_integrity = SetTokenInformation(restricted_token.value, TokenIntegrityLevel, &mandatory, sizeof(mandatory) + GetLengthSid(medium));
    LocalFree(medium);
    if (!assigned_integrity) return 70;
    PROCESS_INFORMATION process{};
    if (!CreateProcessAsUserW(restricted_token.value, executable.c_str(), command.data(), nullptr, nullptr, browser_mode ? TRUE : FALSE,
                        CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT | EXTENDED_STARTUPINFO_PRESENT,
                        environment.data(), windows, &startup.StartupInfo, &process)) return 70;
    Handle child{process.hProcess}, thread{process.hThread};
    if (!AssignProcessToJobObject(job.value, child.value)) { TerminateProcess(child.value, 70); return 70; }
    Handle token;
    DWORD token_bytes = 0;
    if (!OpenProcessToken(child.value, TOKEN_QUERY, &token.value)) return 70;
    TOKEN_ELEVATION elevation{};
    DWORD elevation_bytes = 0;
    if (!GetTokenInformation(token.value, TokenElevation, &elevation, sizeof(elevation), &elevation_bytes) || elevation.TokenIsElevated) return 70;
    GetTokenInformation(token.value, TokenAppContainerSid, nullptr, 0, &token_bytes);
    std::vector<unsigned char> token_data(token_bytes);
    if (!GetTokenInformation(token.value, TokenAppContainerSid, token_data.data(), token_bytes, &token_bytes)) return 70;
    auto package = reinterpret_cast<TOKEN_APPCONTAINER_INFORMATION*>(token_data.data())->TokenAppContainer;
    if (!package || !IsValidSid(package) || !EqualSid(package, fence.package_sid())) return 70;
    if (!browser_mode) std::wcout << L"package=" << fence.sid_string() << L" profile=" << fence.profile() << std::endl;
    if (ResumeThread(thread.value) == static_cast<DWORD>(-1)) return 70;
    DWORD result;
    if (browser_mode) {
      // Only this controller owns stdin/stdout. Chromium receives two private
      // anonymous CDP handles, never an exposed debugging TCP listener.
      CloseHandle(browser_input.value); browser_input.value = nullptr;
      CloseHandle(browser_output.value); browser_output.value = nullptr;
      Handle stopped{CreateEventW(nullptr, TRUE, FALSE, nullptr)};
      if (!stopped.value) return 70;
      const auto wide_sid = fence.sid_string();
      const std::string ascii_sid(wide_sid.begin(), wide_sid.end());
      // A byte JSON line on private CDP transport, independent of CRT wide-
      // console encoding after UAC pipe bootstrap.
      std::cout << "{\"protocol\":1,\"pid\":" << process.dwProcessId << ",\"proxy_port\":" << port
                << ",\"package_sid\":\"" << ascii_sid << "\"}" << std::endl;
      auto relay = [&](HANDLE from, HANDLE to) {
        char buffer[4096]; DWORD count = 0; size_t total = 0, frame = 0;
        while (ReadFile(from, buffer, sizeof(buffer), &count, nullptr) && count) {
          total += count;
          bool oversized = total > 32 * 1024 * 1024;
          for (DWORD index = 0; index < count; ++index) { frame = buffer[index] == '\0' ? 0 : frame + 1; oversized = oversized || frame > 1024 * 1024; }
          if (oversized) break;
          DWORD offset = 0;
          while (offset < count) { DWORD written = 0; if (!WriteFile(to, buffer + offset, count - offset, &written, nullptr) || !written) { SetEvent(stopped.value); return; } offset += written; }
        }
        SetEvent(stopped.value);
      };
      std::thread commands(relay, GetStdHandle(STD_INPUT_HANDLE), parent_input.value);
      std::thread responses(relay, parent_output.value, GetStdHandle(STD_OUTPUT_HANDLE));
      HANDLE waits[] = {child.value, stopped.value};
      result = WaitForMultipleObjects(2, waits, FALSE, 600000);
      TerminateJobObject(job.value, 70);
      SetEvent(stopped.value);
      HANDLE relay_threads[] = {commands.native_handle(), responses.native_handle()};
      bool relays_stopped = false;
      for (unsigned retry = 0; retry < 100; ++retry) {
        CancelSynchronousIo(commands.native_handle()); CancelSynchronousIo(responses.native_handle());
        if (WaitForMultipleObjects(2, relay_threads, TRUE, 20) == WAIT_OBJECT_0) { relays_stopped = true; break; }
      }
      // Do not hang on an IO-cancellation race. Exiting closes the owned job;
      // persistent package deny rules deliberately remain for recovery.
      if (!relays_stopped) ExitProcess(70);
      commands.join(); responses.join();
    } else result = WaitForSingleObject(child.value, 120000);
    DWORD exit_code = 70;
    if (result == WAIT_OBJECT_0) GetExitCodeProcess(child.value, &exit_code);
    TerminateJobObject(job.value, 70);
    // Descendants must be gone before any persistent deny rule is removed.
    for (unsigned index = 0; index < 100; ++index) {
      if (deployment ? deployment->finish(fence, job.value) : fence.remove_after_job_empty(job.value)) return static_cast<int>(exit_code);
      Sleep(20);
    }
    std::wcerr << L"Deny policy retained; administrative recovery required." << std::endl;
    return 70;
  } catch (...) {
    std::wcerr << L"Isolation refused. No unrestricted child fallback." << std::endl;
    return 70;
  }
}
