import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { MemeSortClient } from "../../api/tauri-client";
import { tauriErrorDetail } from "../../api/tauri-error";
import { useImportBatch } from "../import/ImportBatchContext";
import { importWorkIsActive } from "../import/import-status";

interface LibraryImportMenuProps {
  client: MemeSortClient;
}

type LibraryNotice = { kind: "error" | "success"; text: string };

/**
 * Library top-bar Import entry (ticket 13).
 *
 * One compact menu owns Choose Files and Choose Folder. It reuses the safe
 * native pickers (`chooseLibraryFiles`/`chooseLibraryFolder`) and passes only
 * the returned selection ID to `startLibraryImport`, so raw paths never reach
 * React. Picker cancellation (`null`) causes no mutation and no toast.
 * Active-batch gating, pause/resume (via ImportBatchPanel), validation,
 * feedback, and query invalidation match the previous Library flow.
 */
export function LibraryImportMenu({ client }: LibraryImportMenuProps) {
  const queryClient = useQueryClient();
  const importBatch = useImportBatch();
  const startBatch = importBatch.startBatch;
  const importWorkActive = importWorkIsActive(importBatch.snapshot);
  const [menuOpen, setMenuOpen] = useState(false);
  const [libraryBusy, setLibraryBusy] = useState<"files" | "folder" | null>(null);
  const [libraryNotice, setLibraryNotice] = useState<LibraryNotice | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);

  const controlsDisabled = libraryBusy !== null || importWorkActive;

  useEffect(() => {
    if (!menuOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    const closeOnOutsidePointer = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    };
    window.addEventListener("keydown", closeOnEscape);
    document.addEventListener("mousedown", closeOnOutsidePointer);
    return () => {
      window.removeEventListener("keydown", closeOnEscape);
      document.removeEventListener("mousedown", closeOnOutsidePointer);
    };
  }, [menuOpen]);

  const startLibrarySelection = async (kind: "files" | "folder") => {
    setLibraryBusy(kind);
    setLibraryNotice(null);
    try {
      const selection =
        kind === "files" ? await client.chooseLibraryFiles() : await client.chooseLibraryFolder();
      // Picker cancellation is a no-op: no mutation, no error toast.
      if (!selection) return;
      const label = kind === "files" ? "file" : "folder";
      const count = selection.count;
      setLibraryNotice({
        kind: "success",
        text: `Starting Import Batch for ${count} ${label}${count === 1 ? "" : "s"}.`,
      });
      // Only the opaque selection ID crosses into the import command.
      await startBatch(() => client.startLibraryImport(selection.selection_id));
      setLibraryNotice({
        kind: "success",
        text: `Import Batch started for ${count} ${label}${count === 1 ? "" : "s"}.`,
      });
      await Promise.all([queryClient.invalidateQueries({ queryKey: ["app-state"] })]);
    } catch (error) {
      setLibraryNotice({
        kind: "error",
        text: tauriErrorDetail(
          error,
          "MemeSort could not start the Library Import Batch. Make a fresh native selection to retry.",
        ),
      });
    } finally {
      setLibraryBusy(null);
    }
  };

  const closeAndStartSelection = (kind: "files" | "folder") => {
    setMenuOpen(false);
    void startLibrarySelection(kind);
  };

  return (
    <div className="library-import-menu" ref={menuRef}>
      <button
        className="button"
        type="button"
        aria-haspopup="menu"
        aria-expanded={menuOpen}
        aria-controls="library-import-menu-list"
        disabled={controlsDisabled}
        onClick={() => setMenuOpen((open) => !open)}
      >
        Import
      </button>
      {menuOpen ? (
        <div
          className="library-import-dropdown"
          id="library-import-menu-list"
          role="menu"
          aria-label="Import options"
        >
          <button
            className="button button-secondary library-import-option"
            type="button"
            role="menuitem"
            disabled={controlsDisabled}
            onClick={() => closeAndStartSelection("files")}
          >
            Choose Files
          </button>
          <button
            className="button button-secondary library-import-option"
            type="button"
            role="menuitem"
            disabled={controlsDisabled}
            onClick={() => closeAndStartSelection("folder")}
          >
            Choose Folder
          </button>
        </div>
      ) : null}
      {libraryNotice ? (
        <section
          className={`notice ${libraryNotice.kind === "error" ? "notice-warning" : "notice-success"}`}
          role={libraryNotice.kind === "error" ? "alert" : "status"}
        >
          <span>{libraryNotice.text}</span>
        </section>
      ) : null}
    </div>
  );
}

export default LibraryImportMenu;
