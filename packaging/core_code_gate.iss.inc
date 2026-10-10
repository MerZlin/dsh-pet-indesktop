// Trusted native equivalents of the Core code and Setup project-directory
// barriers.  These routines never close a user process and never enumerate the
// portable data tree: Core program files and user-owned data have separate
// boundaries.
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
function CodeGetVolumeInformation(VolumePath: String; VolumeName: String; VolumeNameSize: LongWord; var Serial, MaxComponent, Flags: LongWord; FileSystemName: String; FileSystemNameSize: LongWord): Integer;
  external 'GetVolumeInformationW@kernel32.dll stdcall';

const
  CodeReparsePoint = $400;
  CodeDirectory = $10;
  CodeInvalidHandle = $FFFFFFFF;
  CodePortableMarker = '{"data":"data","format_version":1,"product_id":"dsh-pet-core-webm"}';

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

function CodeNormalizedRoot(Path: String): String;
begin
  Result := RemoveBackslashUnlessRoot(ExpandFileName(Path));
end;

function CodePathSafe(Path: String): Boolean;
var Current, Parent: String; Attributes, Error: LongWord;
begin
  Result := False;
  Current := ExpandFileName(Path);
  while Current <> '' do begin
    Attributes := CodeGetAttributes(Current);
    if Attributes = CodeInvalidHandle then begin
      Error := CodeGetLastError();
      if (Error <> 2) and (Error <> 3) then Exit;
    end else if (Attributes and CodeReparsePoint) <> 0 then Exit;
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
    Result := ((Information.Attributes and (CodeReparsePoint or CodeDirectory)) = 0) and (Information.NumberOfLinks = 1);
end;

function CodeExistingExecutableSafe(Path: String): Boolean;
var Attributes, Error: LongWord; Handle: TCodeHandle;
begin
  Result := False;
  if not CodePathSafe(Path) then Exit;
  Attributes := CodeGetAttributes(Path);
  if Attributes = CodeInvalidHandle then begin
    Error := CodeGetLastError();
    Result := (Error = 2) or (Error = 3); Exit;
  end;
  Handle := CodeCreateFile(Path, $80, 7, 0, 3, $00200000, 0);
  if Handle = CodeInvalidHandle then Exit;
  Result := CodeSingleFile(Handle);
  CodeCloseHandle(Handle);
end;

function CodeDirectoryBoundarySafe(Root: String): Boolean;
var Attributes: LongWord;
begin
  Result := True;
  if not CodePathSafe(Root) then begin Result := False; Exit; end;
  Attributes := CodeGetAttributes(Root);
  if Attributes = CodeInvalidHandle then Exit;
  Result := ((Attributes and CodeDirectory) <> 0) and ((Attributes and CodeReparsePoint) = 0);
end;

function CodeDataBoundarySafe(Root: String): Boolean;
begin
  Result := CodeDirectoryBoundarySafe(Root);
  if Result then Result := CodeDirectoryBoundarySafe(AddBackslash(Root) + 'plugins');
end;

function CodePortableMarkerValid(Path: String): Boolean;
var Content: AnsiString;
begin
  Result := FileExists(Path) and LoadStringFromFile(Path, Content) and (Content = CodePortableMarker);
end;

function CodeDirectoryEmpty(Root: String): Boolean;
var Scan: TFindRec;
begin
  Result := True;
  if not FindFirst(AddBackslash(Root) + '*', Scan) then Exit;
  try
    repeat
      if (Scan.Name <> '.') and (Scan.Name <> '..') and (CompareText(Scan.Name, '.core-files.lock') <> 0) then begin
        Result := False; Exit;
      end;
    until not FindNext(Scan);
  finally
    FindClose(Scan);
  end;
end;

// The only non-installed reuse case: a prior Setup left its owned data
// receipt, and no unrelated top-level content has since been added.
function CodeRetainedDataSafe(Root: String): Boolean;
var Scan: TFindRec; DataRoot, Receipt: String;
begin
  Result := False;
  DataRoot := AddBackslash(Root) + 'data';
  Receipt := AddBackslash(DataRoot) + '.setup-project.json';
  if not DirExists(DataRoot) then Exit;
  if not CodeDataBoundarySafe(DataRoot) then Exit;
  if not CodeExistingExecutableSafe(Receipt) or not CodePortableMarkerValid(Receipt) then Exit;
  if not FindFirst(AddBackslash(Root) + '*', Scan) then Exit;
  try
    repeat
      if (Scan.Name <> '.') and (Scan.Name <> '..') and
         (CompareText(Scan.Name, 'data') <> 0) and (CompareText(Scan.Name, '.core-files.lock') <> 0) then Exit;
    until not FindNext(Scan);
    Result := True;
  finally
    FindClose(Scan);
  end;
end;

function CodeIsNtfs(Root: String): Boolean;
var VolumePath, VolumeName, FileSystemName: String; Serial, MaxComponent, Flags: LongWord;
begin
  Result := False;
  // Setup targets are local absolute paths.  Derive the volume root instead of
  // passing an output String through GetVolumePathNameW: Inno's external String
  // parameters are buffers, not Delphi-style var strings.
  VolumePath := ExtractFileDrive(Root);
  if VolumePath = '' then Exit;
  if (Length(VolumePath) = 2) and (VolumePath[2] = ':') then
    VolumePath := VolumePath + '\';
  VolumeName := StringOfChar(#0, 256);
  FileSystemName := StringOfChar(#0, 256);
  if CodeGetVolumeInformation(VolumePath, VolumeName, 256, Serial, MaxComponent, Flags, FileSystemName, 256) = 0 then Exit;
  Result := CompareText(Copy(FileSystemName, 1, 4), 'NTFS') = 0;
end;

// Inspect existing Core descendants but deliberately treat data and
// data\\plugins as boundary-only directories. Personal files are not a code
// inventory and must not make an install barrier scale with their size.
function CodeTreeSafe(Root: String; Depth: Integer; var Count: LongWord): Boolean;
var Scan: TFindRec; Path: String; Attributes: LongWord;
begin
  Result := False;
  if (Depth > 64) or (not CodePathSafe(Root)) then Exit;
  Attributes := CodeGetAttributes(Root);
  if (Attributes = CodeInvalidHandle) or ((Attributes and CodeDirectory) = 0) or ((Attributes and CodeReparsePoint) <> 0) then Exit;
  if not FindFirst(AddBackslash(Root) + '*', Scan) then begin Result := True; Exit; end;
  try
    repeat
      if (Scan.Name <> '.') and (Scan.Name <> '..') then begin
        Count := Count + 1;
        if (Count > 50000) or ((Scan.Attributes and CodeReparsePoint) <> 0) then Exit;
        Path := AddBackslash(Root) + Scan.Name;
        if CompareText(Scan.Name, 'data') = 0 then begin
          if ((Scan.Attributes and CodeDirectory) = 0) or (not CodeDataBoundarySafe(Path)) then Exit;
        end else if (Scan.Attributes and CodeDirectory) <> 0 then begin
          if not CodeTreeSafe(Path, Depth + 1, Count) then Exit;
        end else if not CodeExistingExecutableSafe(Path) then Exit;
      end;
    until not FindNext(Scan);
    Result := True;
  finally
    FindClose(Scan);
  end;
end;

// Validate a bounded deletion tree without following reparse points.  The
// caller still performs the actual delete only after this complete pass; the
// data tree is intentionally optional at the project-code call site so large
// personal files do not become an install/removal code-barrier scan.
function CodeDeletionTreeSafe(Root: String; Depth: Integer; var Count: LongWord; SkipData: Boolean): Boolean;
var Scan: TFindRec; Path: String; Attributes: LongWord;
begin
  Result := False;
  if (Depth > 64) or (not CodePathSafe(Root)) then Exit;
  Attributes := CodeGetAttributes(Root);
  if Attributes = CodeInvalidHandle then begin
    Result := not FileExists(Root); Exit;
  end;
  if ((Attributes and CodeDirectory) = 0) or ((Attributes and CodeReparsePoint) <> 0) then Exit;
  if not FindFirst(AddBackslash(Root) + '*', Scan) then begin Result := True; Exit; end;
  try
    repeat
      if (Scan.Name <> '.') and (Scan.Name <> '..') then begin
        if not (SkipData and (CompareText(Scan.Name, 'data') = 0)) then begin
          Count := Count + 1;
          if Count > 50000 then Exit;
          Path := AddBackslash(Root) + Scan.Name;
          Attributes := CodeGetAttributes(Path);
          if (Attributes = CodeInvalidHandle) or ((Attributes and CodeReparsePoint) <> 0) then Exit;
          if (Attributes and CodeDirectory) <> 0 then begin
            if not CodeDeletionTreeSafe(Path, Depth + 1, Count, False) then Exit;
          end else if not CodeExistingExecutableSafe(Path) then Exit;
        end;
      end;
    until not FindNext(Scan);
    Result := True;
  finally
    FindClose(Scan);
  end;
end;

function CodeProductIdentitySafe(Root: String): Boolean;
begin
  Result := CodePathSafe(Root) and CodePortableMarkerValid(AddBackslash(Root) + 'portable.json') and
    FileExists(AddBackslash(Root) + 'dsh-pet-core-webm.exe') and
    CodeExistingExecutableSafe(AddBackslash(Root) + 'dsh-pet-core-webm.exe') and
    CodeDataBoundarySafe(AddBackslash(Root) + 'data');
end;

function AcquireCodeBarrier(Root: String; var Reason: String): Boolean;
var Path: String; Overlapped: TCodeOverlapped; Error, Count: LongWord;
begin
  Result := False;
  ReleaseCodeBarrier();
  Root := CodeNormalizedRoot(Root);
  Reason := 'code_boundary_invalid';
  if (Root = '') or (Length(Root) <= 3) or (Copy(Root, 2, 2) <> ':\') then Exit;
  Path := AddBackslash(Root) + '.core-files.lock';
  if not CodeExistingExecutableSafe(AddBackslash(Root) + 'dsh-pet-core-webm.exe') then Exit;
  if (not CodePathSafe(Root)) or (not CodePathSafe(Path)) then Exit;
  if FileExists(AddBackslash(Root) + 'portable.json') and (not CodePortableMarkerValid(AddBackslash(Root) + 'portable.json')) then begin
    Reason := 'portable_marker_invalid'; Exit;
  end;
  Reason := 'code_root_unavailable';
  if not ForceDirectories(Root) then Exit;
  if (not CodePathSafe(Root)) or (not CodePathSafe(Path)) then begin
    Reason := 'code_boundary_invalid'; Exit;
  end;
  CodeBarrierHandle := CodeCreateFile(Path, $C0000000, 7, 0, 4, $00200000, 0);
  if CodeBarrierHandle = CodeInvalidHandle then Exit;
  CodeBarrierHeld := True;
  if (not CodePathSafe(Root)) or (not CodePathSafe(Path)) or (not CodeSingleFile(CodeBarrierHandle)) then begin
    Reason := 'code_boundary_invalid'; ReleaseCodeBarrier(); Exit;
  end;
  Overlapped.Internal := 0; Overlapped.InternalHigh := 0;
  Overlapped.Offset := 0; Overlapped.OffsetHigh := 0; Overlapped.Event := 0;
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

function AcquirePortableCodeBarrier(Root: String; var Reason: String): Boolean;
begin
  Root := CodeNormalizedRoot(Root);
  Reason := 'portable_marker_invalid';
  if not CodeProductIdentitySafe(Root) then begin Result := False; Exit; end;
  Result := AcquireCodeBarrier(Root, Reason);
end;

function ValidateSetupTarget(Root: String; var Reason: String): Boolean;
begin
  Result := False;
  Reason := 'install_target_invalid';
  // Validate before normalization: ExpandFileName must not silently turn a
  // caller-supplied relative path into an allowed absolute target.
  if (Length(Root) < 3) or (Copy(Root, 2, 2) <> ':\') then Exit;
  Root := CodeNormalizedRoot(Root);
  if Length(Root) <= 3 then begin Reason := 'install_target_root'; Exit; end;
  if not CodeIsNtfs(Root) then begin Reason := 'install_target_not_ntfs'; Exit; end;
  if not CodePathSafe(Root) then begin Reason := 'install_target_reparse'; Exit; end;
  if DirExists(Root) then begin
    if FileExists(AddBackslash(Root) + 'portable.json') then begin
      if not CodeProductIdentitySafe(Root) then begin Reason := 'portable_product_invalid'; Exit; end;
    end else if not CodeDirectoryEmpty(Root) and not CodeRetainedDataSafe(Root) then begin
      Reason := 'install_target_not_empty'; Exit;
    end;
  end else if FileExists(Root) then begin
    Reason := 'install_target_not_directory'; Exit;
  end;
  Reason := ''; Result := True;
end;

function AcquireSetupBarrier(Root: String; var Reason: String): Boolean;
var Path: String; Overlapped: TCodeOverlapped; Error, Count: LongWord;
begin
  Result := False;
  ReleaseCodeBarrier();
  if not ValidateSetupTarget(Root, Reason) then Exit;
  Root := CodeNormalizedRoot(Root);
  Reason := 'install_target_unavailable';
  if not ForceDirectories(Root) then Exit;
  if not CodePathSafe(Root) then begin Reason := 'install_target_reparse'; Exit; end;
  Path := AddBackslash(Root) + '.core-files.lock';
  CodeBarrierHandle := CodeCreateFile(Path, $C0000000, 7, 0, 4, $00200000, 0);
  if CodeBarrierHandle = CodeInvalidHandle then Exit;
  CodeBarrierHeld := True;
  if (not CodePathSafe(Root)) or (not CodePathSafe(Path)) or (not CodeSingleFile(CodeBarrierHandle)) then begin
    Reason := 'install_target_reparse'; ReleaseCodeBarrier(); Exit;
  end;
  Overlapped.Internal := 0; Overlapped.InternalHigh := 0; Overlapped.Offset := 0; Overlapped.OffsetHigh := 0; Overlapped.Event := 0;
  if CodeLockFile(CodeBarrierHandle, 3, 0, 1, 0, Overlapped) = 0 then begin
    Error := CodeGetLastError();
    if (Error = 33) or (Error = 32) then Reason := 'core_in_use';
    ReleaseCodeBarrier(); Exit;
  end;
  if not ValidateSetupTarget(Root, Reason) then begin ReleaseCodeBarrier(); Exit; end;
  Count := 0;
  if not CodeTreeSafe(Root, 0, Count) then begin Reason := 'install_target_boundary_invalid'; ReleaseCodeBarrier(); Exit; end;
  Reason := ''; Result := True;
end;
