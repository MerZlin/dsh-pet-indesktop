/* Owned diagnostic canary. No CRT, GUI DLL, screenshot, or actual credential. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <wincrypt.h>

static BYTE input[4096];
/* A real relocation is required for lowbox image loading. */
static BYTE *volatile input_address = input;

static void emit(const char *text, DWORD size) {
    DWORD written;
    WriteFile(GetStdHandle(STD_OUTPUT_HANDLE), text, size, &written, NULL);
}

void mainCRTStartup(void) {
    DWORD size = 0, count = 0;
    DATA_BLOB source, plain = {0};
    typedef BOOL (WINAPI *UNPROTECT)(DATA_BLOB*, LPWSTR*, DATA_BLOB*, PVOID, CRYPTPROTECT_PROMPTSTRUCT*, DWORD, DATA_BLOB*);
    UNPROTECT unprotect;
    HMODULE library;
    emit("{\"native_ready\":true}\n", 22);
    ReadFile(GetStdHandle(STD_INPUT_HANDLE), &size, sizeof(size), &count, NULL);
    if (count != sizeof(size) || size > sizeof(input)) {
        ExitProcess(2);
    }
    ReadFile(GetStdHandle(STD_INPUT_HANDLE), input_address, size, &count, NULL);
    if (count != size) {
        ExitProcess(3);
    }
    source.cbData = size;
    source.pbData = input;
    library = LoadLibraryW(L"crypt32.dll");
    unprotect = library ? (UNPROTECT)GetProcAddress(library, "CryptUnprotectData") : NULL;
    if (unprotect && unprotect(&source, NULL, NULL, NULL, NULL, CRYPTPROTECT_UI_FORBIDDEN, &plain)) {
        const char text[] = "{\"dpapi_read\":true}\n";
        emit(text, sizeof(text)-1);
        SecureZeroMemory(plain.pbData, plain.cbData);
        LocalFree(plain.pbData);
    } else {
        const char text[] = "{\"dpapi_read\":false}\n";
        emit(text, sizeof(text)-1);
    }
    if (library) FreeLibrary(library);
    /* No call reaches a desktop. Explicitly diagnose loader incompatibility. */
    library = LoadLibraryW(L"user32.dll");
    if (library) {
        const char text[] = "{\"user32_loaded\":true}\n";
        emit(text, sizeof(text)-1);
        FreeLibrary(library);
    } else {
        const char text[] = "{\"user32_loaded\":false}\n";
        emit(text, sizeof(text)-1);
    }
    ExitProcess(0);
}
