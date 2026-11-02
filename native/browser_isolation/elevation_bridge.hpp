#pragma once
#include <string>
#include <vector>

namespace jarvis {
void require_protected_broker();
int request_managed_browser(const std::wstring& port, const std::wstring& browser);
// Connects only to the same user's same protected executable, not a service.
std::vector<std::wstring> accept_managed_pipe(const std::wstring& identity, const std::wstring& launcher_pid);
}
