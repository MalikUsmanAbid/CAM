//! Static startup guidance; never display private pipe frames or raw exceptions.

#[cfg(windows)]
pub fn show_failure() {
    #[link(name = "user32")]
    extern "system" {
        fn MessageBoxW(window: *mut std::ffi::c_void, text: *const u16, caption: *const u16, flags: u32) -> i32;
    }
    let message: Vec<u16> = concat!(
        "CAM could not start its private desktop backend.\n\n",
        "1. Close another JARVIS instance and relaunch.\n",
        "2. If model settings are invalid, launch with --reset-settings. Portable users can use Reset-Settings.cmd. Previous settings generations are retained.\n",
        "3. A database needing migration requires a separately authorized backup and migration. Startup will not migrate it automatically.\n",
        "4. Confirm the packaged backend and Microsoft WebView2 runtime are present.\n\n",
        "No automatic reset, migration or reinstall was attempted. Do not share tokens, credentials or private protocol output."
    ).encode_utf16().chain(Some(0)).collect();
    let caption: Vec<u16> = "CAM — startup recovery".encode_utf16().chain(Some(0)).collect();
    unsafe { MessageBoxW(std::ptr::null_mut(), message.as_ptr(), caption.as_ptr(), 0x10); }
}

#[cfg(not(windows))]
pub fn show_failure() {
    eprintln!("Desktop startup failed. Review packaged runtime, settings and explicit migration requirements.");
}
