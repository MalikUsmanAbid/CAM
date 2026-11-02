#pragma once
#include "browser_fence.hpp"

namespace jarvis {
// Explicit elevated, interactive, same-user maintenance boundary. Not a service.
// One managed controller at a time; current-boot crashed sessions stay denied.
class ManagedDeployment final {
 public:
  ManagedDeployment();
  ~ManagedDeployment();
  ManagedDeployment(const ManagedDeployment&) = delete;
  ManagedDeployment& operator=(const ManagedDeployment&) = delete;
  void prepare(const BrowserFence& fence);
  void enable_loopback(const BrowserFence& fence);
  bool finish(BrowserFence& fence, HANDLE empty_job);
 private:
  HANDLE mutex_ = nullptr;
  HKEY records_ = nullptr;
  std::wstring epoch_, record_, actor_;
  void recover_previous_boot();
};
}
