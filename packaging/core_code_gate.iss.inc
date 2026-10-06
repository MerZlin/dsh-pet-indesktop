// Trusted native equivalent of pet.core_code_gate. Never closes user processes.
// Inno Setup 6 runs this script in its 32-bit process: pointer-sized fields in
// OVERLAPPED are 32-bit even when the product Core itself is 64-bit.
type
  TCodeHandle = LongWord;
  TCodeOverlapped = record
    Internal: LongWord;
    InternalHigh: LongWord;
    Offset: LongWord;
    OffsetHigh: LongWord;
    Event: TCodeHandle;
  end;
  TCodeFileInformation = record
    Attributes: LongWord;
    CreationLow: LongWord; CreationHigh: LongWord;
    AccessLow: LongWord; AccessHigh: LongWord;
    WriteLow: LongWord; WriteHigh: LongWord;
    VolumeSerial: LongWord;
    SizeHigh: LongWord; SizeLow: LongWord;
    NumberOfLinks: LongWord;
    IndexHigh: LongWord; IndexLow: LongWord;
  end;

function CodeCreateFile(Name: String; Access, Share, Security, Creation, Flags: LongWord; Template: TCodeHandle): TCodeHandle;
  external 'CreateFileW@kernel32.dll stdcall';
function CodeLockFile(Handle: TCodeHandle; Flags, Reserved, Low, High: LongWord; var Overlapped: TCodeOverlapped): Integer;
  external 'LockFileEx@kernel32.dll stdcall';
function CodeCloseHandle(Handle: TCodeHandle): Integer;
  external 'CloseHandle@kernel32.dll stdcall';
function CodeGetAttributes(Name: String): LongWord;
  external 'GetFileAttributesW@kernel32.dll stdcall';
function CodeGetInformation(Handle: TCodeHandle; var Information: TCodeFileInformation): Integer;
  external 'GetFileInformationByHandle@kernel32.dll stdcall';
function CodeGetLastError(): LongWord;
  external 'GetLastError@kernel32.dll stdcall';

var
  CodeBarrierHandle: TCodeHandle;
  CodeBarrierHeld: Boolean;

procedure ReleaseCodeBarrier();
begin
  if CodeBarrierHeld then begin
    CodeCloseHandle(CodeBarrierHandle);
    CodeBarrierHeld := False;
  end;
end;

function CodePathSafe(Path: String): Boolean;
var Current, Parent: String; Attributes, Error: LongWord;
begin
  Result := False;
  Current := ExpandFileName(Path);
  while Current <> '' do begin
    Attributes := CodeGetAttributes(Current);
    if Attributes = $FFFFFFFF then begin
      Error := CodeGetLastError();
      if (Error <> 2) and (Error <> 3) then Exit;
    end else if (Attributes and $400) <> 0 then Exit;
    Parent := ExtractFileDir(Current);
    if Parent = Current then Break;
    Current := Parent;
  end;
  Result := True;
end;

function CodeSingleFile(Handle: TCodeHandle): Boolean;
var Information: TCodeFileInformation;
begin
  Result := (CodeGetInformation(Handle, Information) <> 0);
  if Result then
    Result := ((Information.Attributes and ($400 or $10)) = 0) and (Information.NumberOfLinks = 1);
end;

function CodeExistingExecutableSafe(Path: String): Boolean;
var Attributes, Error: LongWord; Handle: TCodeHandle;
begin
  Result := False;
  if not CodePathSafe(Path) then Exit;
  Attributes := CodeGetAttributes(Path);
  if Attributes = $FFFFFFFF then begin
    Error := CodeGetLastError();
    Result := (Error = 2) or (Error = 3); Exit;
  end;
  Handle := CodeCreateFile(Path, $80, 7, 0, 3, $00200000, 0);
  if Handle = $FFFFFFFF then Exit;
  Result := CodeSingleFile(Handle);
  CodeCloseHandle(Handle);
end;

// Inspect existing code descendants as well as the root. A DLL hardlink or
// junction under _internal is just as unsafe as a linked executable. Bounds
// reject unexpectedly broad targets rather than traversing unrelated data.
function CodeTreeSafe(Root: String; Depth: Integer; var Count: LongWord): Boolean;
var Scan: TFindRec; Path: String; Attributes: LongWord;
begin
  Result := False;
  if (Depth > 64) or (not CodePathSafe(Root)) then Exit;
  Attributes := CodeGetAttributes(Root);
  if (Attributes = $FFFFFFFF) or ((Attributes and $10) = 0) then Exit;
  if not FindFirst(AddBackslash(Root) + '*', Scan) then Exit;
  try
    repeat
      if (Scan.Name <> '.') and (Scan.Name <> '..') then begin
        Count := Count + 1;
        if (Count > 50000) or ((Scan.Attributes and $400) <> 0) then Exit;
        Path := AddBackslash(Root) + Scan.Name;
        if (Scan.Attributes and $10) <> 0 then begin
          if not CodeTreeSafe(Path, Depth + 1, Count) then Exit;
        end else if not CodeExistingExecutableSafe(Path) then Exit;
      end;
    until not FindNext(Scan);
    Result := True;
  finally
    FindClose(Scan);
  end;
end;

function AcquireCodeBarrier(Root: String; var Reason: String): Boolean;
var Path: String; Overlapped: TCodeOverlapped; Error, Count: LongWord;
begin
  Result := False;
  ReleaseCodeBarrier();
  Reason := 'code_boundary_invalid';
  if (Root = '') or (ExpandFileName(Root) <> Root) or (Length(Root) < 3) then Exit;
  if (Copy(Root, 2, 2) <> ':\') then Exit;
  Path := AddBackslash(Root) + '.core-files.lock';
  if not CodeExistingExecutableSafe(AddBackslash(Root) + 'dsh-pet-core-webm.exe') then Exit;
  if (not CodePathSafe(Root)) or (not CodePathSafe(Path)) then Exit;
  if FileExists(AddBackslash(Root) + 'portable.json') then begin
    Reason := 'portable_setup_forbidden'; Exit;
  end;
  Reason := 'code_root_unavailable';
  if not ForceDirectories(Root) then Exit;
  if (not CodePathSafe(Root)) or (not CodePathSafe(Path)) then begin
    Reason := 'code_boundary_invalid'; Exit;
  end;
  // No inherited handle/security descriptor. Open the final reparse point itself
  // so it cannot silently redirect before the second boundary check.
  CodeBarrierHandle := CodeCreateFile(Path, $C0000000, 7, 0, 4, $00200000, 0);
  if CodeBarrierHandle = $FFFFFFFF then Exit;
  CodeBarrierHeld := True;
  if (not CodePathSafe(Root)) or (not CodePathSafe(Path)) or (not CodeSingleFile(CodeBarrierHandle)) then begin
    Reason := 'code_boundary_invalid'; ReleaseCodeBarrier(); Exit;
  end;
  Overlapped.Internal := 0; Overlapped.InternalHigh := 0;
  Overlapped.Offset := 0; Overlapped.OffsetHigh := 0; Overlapped.Event := 0;
  // LOCKFILE_FAIL_IMMEDIATELY | LOCKFILE_EXCLUSIVE_LOCK, offset=0,length=1.
  if CodeLockFile(CodeBarrierHandle, 3, 0, 1, 0, Overlapped) = 0 then begin
    Error := CodeGetLastError();
    if (Error = 33) or (Error = 32) then Reason := 'core_in_use';
    ReleaseCodeBarrier(); Exit;
  end;
  if (not CodePathSafe(Root)) or (not CodePathSafe(Path)) then begin
    Reason := 'code_boundary_invalid'; ReleaseCodeBarrier(); Exit;
  end;
  if not CodeExistingExecutableSafe(AddBackslash(Root) + 'dsh-pet-core-webm.exe') then begin
    Reason := 'code_boundary_invalid'; ReleaseCodeBarrier(); Exit;
  end;
  Count := 0;
  if not CodeTreeSafe(Root, 0, Count) then begin
    Reason := 'code_boundary_invalid'; ReleaseCodeBarrier(); Exit;
  end;
  Reason := ''; Result := True;
end;
