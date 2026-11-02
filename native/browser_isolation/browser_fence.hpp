#pragma once
#include <windows.h>
#include <fwpmu.h>
#include <string>
#include <vector>
#include <functional>

namespace jarvis {
// Privileged native source; application activation requires accepted builds.
// Persistent package-scoped blocks survive broker death; never a global rule.
class BrowserFence final {
 public:
  explicit BrowserFence(unsigned short proxy_port, std::function<void(const BrowserFence&)> journal = {});
  ~BrowserFence();
  BrowserFence(const BrowserFence&) = delete;
  BrowserFence& operator=(const BrowserFence&) = delete;
  PSID package_sid() const { return sid_; }
  const std::wstring& profile() const { return profile_; }
  std::wstring sid_string() const;
  const GUID& sublayer() const { return sublayer_; }
  const std::vector<GUID>& filters() const { return filters_; }
  // Caller may remove the fence only after its kill-on-close job is empty.
  // Otherwise destructor closes engine only, preserving deny filters/profile.
  bool remove_after_job_empty(HANDLE job);
 private:
  HANDLE engine_ = nullptr;
  PSID sid_ = nullptr;
  GUID sublayer_{};
  std::wstring profile_;
  std::vector<GUID> filters_;
  bool installed_ = false;
  void add(const GUID& layer, const std::vector<FWPM_FILTER_CONDITION0>& conditions);
};
}
