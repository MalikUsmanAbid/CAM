// Disposable fixture sockets only. No DNS or public Internet destinations.
// A refused/unreachable socket is not accepted as evidence of policy blocking.
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <iostream>
#include <string>

static int connect_loopback(int family, int type, unsigned short port) {
  SOCKET socket_value = socket(family, type, type == SOCK_STREAM ? IPPROTO_TCP : IPPROTO_UDP);
  if (socket_value == INVALID_SOCKET) return WSAGetLastError();
  u_long nonblocking = 1;
  if (ioctlsocket(socket_value, FIONBIO, &nonblocking) != 0) {
    const int error = WSAGetLastError(); closesocket(socket_value); return error;
  }
  sockaddr_storage address{};
  int bytes = 0;
  if (family == AF_INET) {
    auto* value = reinterpret_cast<sockaddr_in*>(&address);
    value->sin_family = AF_INET; value->sin_port = htons(port);
    InetPtonW(AF_INET, L"127.0.0.1", &value->sin_addr); bytes = sizeof(*value);
  } else {
    auto* value = reinterpret_cast<sockaddr_in6*>(&address);
    value->sin6_family = AF_INET6; value->sin6_port = htons(port);
    InetPtonW(AF_INET6, L"::1", &value->sin6_addr); bytes = sizeof(*value);
  }
  int outcome = 0;
  if (connect(socket_value, reinterpret_cast<sockaddr*>(&address), bytes) != 0) {
    outcome = WSAGetLastError();
    if (outcome == WSAEWOULDBLOCK) {
      fd_set ready, failed;
      FD_ZERO(&ready); FD_ZERO(&failed);
      FD_SET(socket_value, &ready); FD_SET(socket_value, &failed);
      timeval timeout{2, 0};
      if (select(0, nullptr, &ready, &failed, &timeout) > 0) {
        int length = sizeof(outcome);
        if (getsockopt(socket_value, SOL_SOCKET, SO_ERROR, reinterpret_cast<char*>(&outcome), &length) != 0) outcome = WSAGetLastError();
      } else outcome = WSAETIMEDOUT;
    }
  }
  if (outcome == 0 && type == SOCK_DGRAM) {
    const char marker = 'J';
    if (send(socket_value, &marker, 1, 0) == SOCKET_ERROR) outcome = WSAGetLastError();
  }
  closesocket(socket_value);
  return outcome;
}

int wmain(int argc, wchar_t** argv) {
  if (argc != 4 || std::wstring(argv[1]) != L"--disposable-loopback-fixtures") return 64;
  try {
    size_t first = 0, second = 0;
    auto proxy = std::stoul(argv[2], &first), denied = std::stoul(argv[3], &second);
    if (first != wcslen(argv[2]) || second != wcslen(argv[3]) || proxy < 1024 || proxy > 65535 || denied < 1024 || denied > 65535 || proxy == denied) return 64;
    WSADATA data{};
    if (WSAStartup(MAKEWORD(2, 2), &data) != 0) return 70;
    bool passed = true;
    for (int family : {AF_INET, AF_INET6}) {
      for (int type : {SOCK_STREAM, SOCK_DGRAM}) {
        for (auto port : {proxy, denied}) {
          const int result = connect_loopback(family, type, static_cast<unsigned short>(port));
          const bool allowed = family == AF_INET && type == SOCK_STREAM && port == proxy;
          const bool success = allowed ? result == 0 : result == WSAEACCES;
          std::cout << "family=" << family << " transport=" << type << " port=" << port << " error=" << result << " expected=" << (allowed ? "allow" : "policy_denial") << " pass=" << success << std::endl;
          passed = passed && success;
        }
      }
    }
    WSACleanup();
    return passed ? 0 : 1;
  } catch (...) { return 64; }
}
