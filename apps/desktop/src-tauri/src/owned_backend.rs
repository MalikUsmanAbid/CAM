//! Suspended launch -> Job assignment -> resume. Not an OS security sandbox.
use std::{ffi::OsStr, fs::File, io::Write, mem::{size_of, size_of_val, zeroed},
          os::windows::{ffi::OsStrExt, io::FromRawHandle}, path::Path};
use windows_sys::Win32::{
    Foundation::{CloseHandle, SetHandleInformation, HANDLE, HANDLE_FLAG_INHERIT},
    Security::SECURITY_ATTRIBUTES,
    Storage::FileSystem::{CreateFileW, FILE_ATTRIBUTE_NORMAL, FILE_GENERIC_WRITE,
                          FILE_SHARE_READ, FILE_SHARE_WRITE, OPEN_EXISTING},
    System::{JobObjects::{AssignProcessToJobObject, CreateJobObjectW,
                         SetInformationJobObject, JobObjectExtendedLimitInformation,
                         JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE},
             Pipes::CreatePipe,
             Threading::{CreateProcessW, ResumeThread, TerminateProcess, WaitForSingleObject,
                         InitializeProcThreadAttributeList, UpdateProcThreadAttribute,
                         DeleteProcThreadAttributeList, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                         CREATE_NO_WINDOW, CREATE_SUSPENDED, CREATE_UNICODE_ENVIRONMENT, EXTENDED_STARTUPINFO_PRESENT,
                         PROCESS_INFORMATION, STARTUPINFOEXW, STARTF_USESTDHANDLES}},
};

fn wide(value: &OsStr) -> Vec<u16> { value.encode_wide().chain(Some(0)).collect() }

struct Handle(HANDLE);
unsafe impl Send for Handle {}
impl Drop for Handle {
    fn drop(&mut self) { if !self.0.is_null() { unsafe { CloseHandle(self.0); } } }
}
impl Handle {
    fn file(mut self) -> File {
        let raw = self.0;
        self.0 = std::ptr::null_mut();
        unsafe { File::from_raw_handle(raw) }
    }
}

pub struct OwnedBackend {
    job: Handle,
    process: Handle,
    pub input: File,
}

impl OwnedBackend {
    pub fn launch(executable: &Path) -> Result<(Self, File), String> {
        if !executable.is_absolute() || !executable.is_file() {
            return Err("Bundled backend executable is missing.".into());
        }
        unsafe {
            let mut attributes = SECURITY_ATTRIBUTES {
                nLength: size_of::<SECURITY_ATTRIBUTES>() as u32,
                lpSecurityDescriptor: std::ptr::null_mut(), bInheritHandle: 1,
            };
            let pipe = |attributes: &mut SECURITY_ATTRIBUTES| -> Result<(Handle, Handle), String> {
                let (mut read, mut write) = (std::ptr::null_mut(), std::ptr::null_mut());
                if CreatePipe(&mut read, &mut write, attributes, 0) == 0 {
                    return Err("Cannot create private backend pipes.".into());
                }
                Ok((Handle(read), Handle(write)))
            };
            let (input_read, input_write) = pipe(&mut attributes)?;
            let (output_read, output_write) = pipe(&mut attributes)?;
            if SetHandleInformation(input_write.0, HANDLE_FLAG_INHERIT, 0) == 0
                || SetHandleInformation(output_read.0, HANDLE_FLAG_INHERIT, 0) == 0 {
                return Err("Cannot isolate private backend pipe inheritance.".into());
            }
            // Discard child stderr instead of persisting unredacted diagnostics.
            let null_name = wide(OsStr::new("NUL"));
            let stderr = Handle(CreateFileW(null_name.as_ptr(), FILE_GENERIC_WRITE,
                FILE_SHARE_READ | FILE_SHARE_WRITE, &attributes, OPEN_EXISTING,
                FILE_ATTRIBUTE_NORMAL, std::ptr::null_mut()));
            if stderr.0 == (-1isize) as HANDLE { return Err("Cannot configure backend diagnostics.".into()); }
            let job = Handle(CreateJobObjectW(std::ptr::null(), std::ptr::null()));
            if job.0.is_null() { return Err("Cannot create backend process job.".into()); }
            let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = zeroed();
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            if SetInformationJobObject(job.0, JobObjectExtendedLimitInformation,
                &limits as *const _ as *const _, size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32) == 0 {
                return Err("Cannot enforce backend process ownership.".into());
            }
            let image = wide(executable.as_os_str());
            let mut command = wide(OsStr::new(&format!("\"{}\"", executable.display())));
            let cwd = wide(executable.parent().unwrap().as_os_str());
            // Do not leak shell/provider tokens, Python injection settings or
            // developer migration opt-ins into the bundled backend process.
            let mut environment = Vec::<u16>::new();
            for name in ["APPDATA", "LOCALAPPDATA", "SystemRoot", "TEMP", "TMP", "USERPROFILE", "WINDIR"] {
                if let Some(value) = std::env::var_os(name) {
                    let mut entry = std::ffi::OsString::from(name);
                    entry.push("=");
                    entry.push(value);
                    environment.extend(wide(&entry));
                }
            }
            environment.push(0);
            let mut attribute_bytes = 0usize;
            InitializeProcThreadAttributeList(std::ptr::null_mut(), 1, 0, &mut attribute_bytes);
            if attribute_bytes == 0 { return Err("Cannot size backend handle isolation.".into()); }
            let mut attribute_storage = vec![0usize; attribute_bytes.div_ceil(size_of::<usize>())];
            let attribute_list = attribute_storage.as_mut_ptr() as *mut _;
            if InitializeProcThreadAttributeList(attribute_list, 1, 0, &mut attribute_bytes) == 0 {
                return Err("Cannot initialize backend handle isolation.".into());
            }
            let handles = [input_read.0, output_write.0, stderr.0];
            if UpdateProcThreadAttribute(attribute_list, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST as usize,
                handles.as_ptr() as *const _, size_of_val(&handles), std::ptr::null_mut(), std::ptr::null_mut()) == 0 {
                DeleteProcThreadAttributeList(attribute_list);
                return Err("Cannot restrict inherited backend handles.".into());
            }
            let mut startup: STARTUPINFOEXW = zeroed();
            startup.StartupInfo.cb = size_of::<STARTUPINFOEXW>() as u32;
            startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
            startup.StartupInfo.hStdInput = input_read.0;
            startup.StartupInfo.hStdOutput = output_write.0;
            startup.StartupInfo.hStdError = stderr.0;
            startup.lpAttributeList = attribute_list;
            let mut information: PROCESS_INFORMATION = zeroed();
            let created = CreateProcessW(image.as_ptr(), command.as_mut_ptr(), std::ptr::null(),
                std::ptr::null(), 1, CREATE_SUSPENDED | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT | EXTENDED_STARTUPINFO_PRESENT,
                environment.as_ptr() as *const _, cwd.as_ptr(), &startup.StartupInfo, &mut information);
            DeleteProcThreadAttributeList(attribute_list);
            if created == 0 {
                return Err("Cannot launch bundled backend.".into());
            }
            let process = Handle(information.hProcess);
            let thread = Handle(information.hThread);
            if AssignProcessToJobObject(job.0, process.0) == 0 {
                TerminateProcess(process.0, 1);
                return Err("Cannot contain backend process.".into());
            }
            if ResumeThread(thread.0) == u32::MAX {
                return Err("Cannot resume contained backend.".into());
            }
            drop(input_read);
            drop(output_write);
            Ok((Self { job, process, input: input_write.file() }, output_read.file()))
        }
    }

    pub fn stop(&mut self) {
        let _ = self.input.write_all(b"{\"version\":1,\"action\":\"shutdown\"}\n");
        let _ = self.input.flush();
        unsafe { WaitForSingleObject(self.process.0, 5000); }
        // Closing the job on Drop terminates remaining owned descendants.
    }
}

impl Drop for OwnedBackend {
    fn drop(&mut self) {
        self.stop();
        // Keep job ownership alive until graceful stop has been attempted.
        let _ = &self.job;
    }
}
