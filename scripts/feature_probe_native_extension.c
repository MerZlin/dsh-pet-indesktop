/* Trusted headless Win32/libsodium adapter: no ctypes/OLE32 or static USER32 dependency. */
#define PY_SSIZE_T_CLEAN
#define WIN32_LEAN_AND_MEAN
#include <Python.h>
#include <windows.h>
#include <wincrypt.h>
#include <wincred.h>
#include <sddl.h>

static PyObject *credential_read(PyObject *self, PyObject *args) {
    PyObject *name; WCHAR *target; PCREDENTIALW value = NULL; BOOL found;
    if (!PyArg_ParseTuple(args, "U", &name)) return NULL;
    target = PyUnicode_AsWideCharString(name, NULL); if (!target) return NULL;
    found = CredReadW(target, CRED_TYPE_GENERIC, 0, &value);
    if (found) CredFree(value); PyMem_Free(target);
    return PyBool_FromLong(found);
}
static PyObject *dpapi_read(PyObject *self, PyObject *args) {
    char *cipher; Py_ssize_t length; DATA_BLOB source, plain = {0}; BOOL found;
    if (!PyArg_ParseTuple(args, "y#", &cipher, &length)) return NULL;
    if (length > 65536) return PyErr_Format(PyExc_ValueError,"canary input bound");
    source.cbData=(DWORD)length; source.pbData=(BYTE*)cipher;
    found=CryptUnprotectData(&source,NULL,NULL,NULL,NULL,CRYPTPROTECT_UI_FORBIDDEN,&plain);
    if (found) { SecureZeroMemory(plain.pbData, plain.cbData); LocalFree(plain.pbData); }
    return PyBool_FromLong(found);
}
static PyObject *process_read(PyObject *self, PyObject *args) {
    unsigned long pid; HANDLE handle;
    if (!PyArg_ParseTuple(args, "k", &pid)) return NULL;
    handle=OpenProcess(PROCESS_VM_READ,FALSE,pid);
    if (handle) CloseHandle(handle);
    return PyBool_FromLong(handle!=NULL);
}
static PyObject *desktop_access(PyObject *self, PyObject *args) {
    PyObject *name; WCHAR *target; BOOL found=FALSE;
    typedef HDESK (WINAPI *OPEN)(LPCWSTR,DWORD,BOOL,ACCESS_MASK);
    typedef BOOL (WINAPI *CLOSE)(HDESK);
    HMODULE lib; OPEN open; CLOSE close; HDESK desk;
    if (!PyArg_ParseTuple(args, "U", &name)) return NULL;
    target=PyUnicode_AsWideCharString(name,NULL); if (!target) return NULL;
    lib=LoadLibraryW(L"user32.dll");
    if (lib) {
        open=(OPEN)GetProcAddress(lib,"OpenDesktopW"); close=(CLOSE)GetProcAddress(lib,"CloseDesktop");
        if (open && close) { desk=open(target,0,FALSE,DESKTOP_READOBJECTS); if (desk) { found=TRUE; close(desk); } }
        FreeLibrary(lib);
    }
    PyMem_Free(target); return PyBool_FromLong(found);
}
static PyObject *inherited_file_access(PyObject *self, PyObject *args) {
    unsigned long long value; BYTE byte; DWORD count=0; HANDLE handle;
    if (!PyArg_ParseTuple(args, "K", &value)) return NULL;
    handle=(HANDLE)(ULONG_PTR)value;
    return PyBool_FromLong(GetFileType(handle)==FILE_TYPE_DISK && ReadFile(handle,&byte,1,&count,NULL) && count==1);
}
/* Documented token/AccessCheck/mitigation/Job queries only. No candidate or
   environment flag can turn an ordinary process into a sandbox probe. */
static int access_for(HANDLE token, LPCWSTR sddl) {
    PSECURITY_DESCRIPTOR sd=NULL; GENERIC_MAPPING map={1,1,1,1};
    BYTE privileges[1024]; DWORD length=sizeof(privileges), granted=0; BOOL allowed=FALSE, ok;
    if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl,SDDL_REVISION_1,&sd,NULL)) return -1;
    ok=AccessCheck(sd,token,1,&map,(PPRIVILEGE_SET)privileges,&length,&granted,&allowed);
    LocalFree(sd); return ok ? (allowed ? 1 : 0) : -1;
}
static PyObject *sandbox_enforced(PyObject *self, PyObject *args) {
    typedef BOOL (WINAPI *MITIGATION)(HANDLE,int,PVOID,SIZE_T);
    MITIGATION mitigation=(MITIGATION)GetProcAddress(GetModuleHandleW(L"kernel32.dll"),"GetProcessMitigationPolicy");
    HANDLE token=NULL, impersonated=NULL; DWORD app=0,caps[1024]={0},sidbuf[1024]={0},size=0,flags=0;
    BOOL member=FALSE, valid=FALSE; WCHAR *sidtext=NULL; WCHAR own[512]; JOBOBJECT_EXTENDED_LIMIT_INFORMATION job;
    if (!mitigation || !mitigation(GetCurrentProcess(),4,&flags,sizeof(flags)) || !(flags&1)) goto done;
    if (!IsProcessInJob(GetCurrentProcess(),NULL,&member) || !member) goto done;
    ZeroMemory(&job,sizeof(job));
    if (!QueryInformationJobObject(NULL,JobObjectExtendedLimitInformation,&job,sizeof(job),NULL)) goto done;
    if (!(job.BasicLimitInformation.LimitFlags&JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE) ||
        !(job.BasicLimitInformation.LimitFlags&JOB_OBJECT_LIMIT_ACTIVE_PROCESS) || job.BasicLimitInformation.ActiveProcessLimit!=1 ||
        !(job.BasicLimitInformation.LimitFlags&JOB_OBJECT_LIMIT_PROCESS_MEMORY) || job.ProcessMemoryLimit>512ULL*1024*1024 ||
        (job.BasicLimitInformation.LimitFlags&(JOB_OBJECT_LIMIT_BREAKAWAY_OK|JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK))) goto done;
    if (!OpenProcessToken(GetCurrentProcess(),TOKEN_QUERY|TOKEN_DUPLICATE,&token)) goto done;
    if (!GetTokenInformation(token,(TOKEN_INFORMATION_CLASS)29,&app,sizeof(app),&size) || !app) goto done;
    if (!GetTokenInformation(token,(TOKEN_INFORMATION_CLASS)30,caps,sizeof(caps),&size) || caps[0]!=0) goto done;
    if (!GetTokenInformation(token,(TOKEN_INFORMATION_CLASS)31,sidbuf,sizeof(sidbuf),&size)) goto done;
    if (!ConvertSidToStringSidW(*(PSID*)sidbuf,&sidtext) || !DuplicateToken(token,SecurityImpersonation,&impersonated)) goto done;
    if (wcslen(sidtext)>400) goto done;
    swprintf(own,512,L"O:SYG:SYD:(A;;0x1;;;WD)(A;;0x1;;;%ls)",sidtext);
    valid=access_for(impersonated,own)==1 && access_for(impersonated,L"O:SYG:SYD:(A;;0x1;;;WD)(A;;0x1;;;S-1-15-2-1)")==0;
done:
    if (sidtext) LocalFree(sidtext); if (impersonated) CloseHandle(impersonated); if (token) CloseHandle(token);
    return PyBool_FromLong(valid);
}
/* Mature upstream Ed25519 implementation, no CFFI/Python3 facade or GUI.
   Module-relative absolute DLL path; the Core pins the whole helper inventory. */
static PyObject *verify_ed25519(PyObject *self, PyObject *args) {
    const unsigned char *key, *signature, *data; Py_ssize_t klen, slen, length;
    HMODULE own=NULL, library=NULL; WCHAR path[32768], *last; DWORD count;
    typedef int (__cdecl *INIT)(void);
    typedef int (__cdecl *VERIFY)(const unsigned char*, const unsigned char*, unsigned long long, const unsigned char*);
    INIT init; VERIFY verify; int valid;
    if (!PyArg_ParseTuple(args,"y#y#y#",&key,&klen,&signature,&slen,&data,&length)) return NULL;
    if (klen!=32 || slen!=64 || length>65536) return PyErr_Format(PyExc_ValueError,"signature input bound");
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,(LPCWSTR)verify_ed25519,&own)) return PyErr_SetFromWindowsErr(0);
    count=GetModuleFileNameW(own,path,32768); if (!count || count>=32700) return PyErr_Format(PyExc_OSError,"native module path");
    last=wcsrchr(path,L'\\'); if (!last) return PyErr_Format(PyExc_OSError,"native module boundary");
    wcscpy(last+1,L"libsodium.dll");
    library=LoadLibraryExW(path,NULL,0x00000100|0x00001000);
    if (!library) return PyErr_SetFromWindowsErr(0);
    init=(INIT)GetProcAddress(library,"sodium_init"); verify=(VERIFY)GetProcAddress(library,"crypto_sign_verify_detached");
    if (!init || !verify) { FreeLibrary(library); return PyErr_Format(PyExc_OSError,"native crypto exports"); }
    if (init()<0) { FreeLibrary(library); return PyErr_Format(PyExc_OSError,"native crypto initialization"); }
    valid=verify(signature,data,(unsigned long long)length,key)==0; FreeLibrary(library);
    return PyBool_FromLong(valid);
}
static PyMethodDef methods[]={
 {"sandbox_enforced",sandbox_enforced,METH_NOARGS,NULL},
 {"verify_ed25519",verify_ed25519,METH_VARARGS,NULL},
 {"credential_read",credential_read,METH_VARARGS,NULL},
 {"dpapi_read",dpapi_read,METH_VARARGS,NULL},
 {"process_read",process_read,METH_VARARGS,NULL},
 {"desktop_access",desktop_access,METH_VARARGS,NULL},
 {"inherited_file_access",inherited_file_access,METH_VARARGS,NULL},
 {NULL,NULL,0,NULL}};
static struct PyModuleDef module={PyModuleDef_HEAD_INIT,"_dsh_probe_native",NULL,-1,methods};
PyMODINIT_FUNC PyInit__dsh_probe_native(void) { return PyModule_Create(&module); }
