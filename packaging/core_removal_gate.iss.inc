// Retain the common data-root fence across the child maintenance process AND
// Core deletion. Other copies of the new Core share this root, not {app}.
// Uses only fixed caller-supplied product root; no data-tree enumeration/deletion.
var
  RemovalBarrierHandle: TCodeHandle;
  RemovalBarrierHeld: Boolean;

procedure ReleaseRemovalBarrier();
begin
  if RemovalBarrierHeld then begin
    CodeCloseHandle(RemovalBarrierHandle);
    RemovalBarrierHeld := False;
  end;
end;

function AcquireRemovalBarrier(Root: String; var Reason: String): Boolean;
var Path: String; Overlapped: TCodeOverlapped; Error: LongWord;
begin
  Result := False;
  ReleaseRemovalBarrier();
  Reason := 'data_boundary_invalid';
  if (Root = '') or (ExpandFileName(Root) <> Root) or (Length(Root) < 3) then Exit;
  if (Copy(Root, 2, 2) <> ':\') or (not CodePathSafe(Root)) then Exit;
  if not DirExists(Root) then begin
    if not ForceDirectories(Root) then begin
      Reason := 'data_root_unavailable'; Exit;
    end;
  end;
  Path := AddBackslash(Root) + 'core-removal.lock';
  if (not CodePathSafe(Path)) or (not CodeExistingExecutableSafe(Path)) then Exit;
  RemovalBarrierHandle := CodeCreateFile(Path, $C0000000, 7, 0, 4, $00200000, 0);
  if RemovalBarrierHandle = $FFFFFFFF then begin
    Reason := 'data_root_unavailable'; Exit;
  end;
  if not CodeSingleFile(RemovalBarrierHandle) then begin
    CodeCloseHandle(RemovalBarrierHandle); Exit;
  end;
  Overlapped.Internal := 0; Overlapped.InternalHigh := 0;
  Overlapped.Offset := 0; Overlapped.OffsetHigh := 0; Overlapped.Event := 0;
  if CodeLockFile(RemovalBarrierHandle, 3, 0, 1, 0, Overlapped) = 0 then begin
    Error := CodeGetLastError(); CodeCloseHandle(RemovalBarrierHandle);
    if Error = 33 then Reason := 'data_root_in_use'
    else Reason := 'data_root_unavailable';
    Exit;
  end;
  RemovalBarrierHeld := True;
  Result := True; Reason := '';
end;
