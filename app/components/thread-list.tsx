import React, { useState, useEffect, useRef, forwardRef, useImperativeHandle, useMemo } from "react";
import { MoreHorizontal, Users } from "lucide-react";
import styles from "./thread-list.module.css";
import { API_ENDPOINTS } from "../config/api";

const Modal = ({ show, onClose, children }: { show: boolean; onClose: () => void; children: React.ReactNode }) => {
  // Escape closes while open. Registered here so the listener lives and dies
  // with the modal.
  useEffect(() => {
    if (!show) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [show, onClose]);

  if (!show) return null;

  return (
    <div
      className={styles.modalOverlay}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className={styles.modal} role="dialog" aria-modal="true">
        <button className={styles.closeBtn} onClick={onClose} aria-label="Close">
          &times;
        </button>
        {children}
      </div>
    </div>
  );
};

interface Thread {
  _id?: string;
  threadId: string;
  name: string;
  isGroup: boolean;
  createdAt?: string;
  updatedAt?: string;
}

interface ThreadListProps {
  currentThreadId: string | null;
  onThreadSelect: (threadId: string | null, isGroup: boolean, name?: string) => void;
  /* Presentation-only filters owned by the sidebar. */
  searchQuery?: string;
  filter?: "mine" | "lab";
  /* Lets chat.tsx keep the sub-header title fresh when the open thread is
     renamed from the list. */
  onThreadRenamed?: (threadId: string, name: string) => void;
}

/* Day-group label for the list: Today / Yesterday / short date. */
const dayLabel = (iso?: string): string => {
  if (!iso) return "Earlier";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "Earlier";
  const now = new Date();
  const startOfDay = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diffDays = Math.round((startOfDay(now) - startOfDay(d)) / 86400000);
  if (diffDays <= 0) return "Today";
  if (diffDays === 1) return "Yesterday";
  const opts: Intl.DateTimeFormatOptions =
    d.getFullYear() === now.getFullYear()
      ? { weekday: "short", day: "numeric", month: "short" }
      : { day: "numeric", month: "short", year: "numeric" };
  return d.toLocaleDateString(undefined, opts);
};

const ThreadList = forwardRef<any, ThreadListProps>(
  ({ currentThreadId, onThreadSelect, searchQuery, filter, onThreadRenamed }, ref) => {
  const [threads, setThreads] = useState<Thread[]>([]);
  const [editingThreadId, setEditingThreadId] = useState<string | null>(null);
  const [newThreadName, setNewThreadName] = useState<string>("");
  const [showThreadInfo, setShowThreadInfo] = useState<{ id: string, name: string, isGroup: boolean } | null>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [menuFor, setMenuFor] = useState<string | null>(null);
  // Read by the async fetch below so a refresh landing mid-rename cannot
  // clobber the edit: state captured in a closure would be stale, a ref is
  // always current.
  const editingRef = useRef<string | null>(null);
  useEffect(() => {
    editingRef.current = editingThreadId;
  }, [editingThreadId]);

  // Escape closes an open row menu.
  useEffect(() => {
    if (!menuFor) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenuFor(null);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [menuFor]);

  const fetchThreads = async () => {
    const response = await fetch(API_ENDPOINTS.getThreadHistory(), { credentials: "include" });
    const data = await response.json();
    // A refresh that lands while a rename edit is open (or its PUT is still
    // in flight) would overwrite the input / optimistic name with the
    // server's stale copy — drop this result; the rename flow refreshes
    // again when it settles.
    if (editingRef.current) return;
    const raw: Thread[] = data.threads || [];
    const seen = new Set<string>();
    const unique = raw.filter((t) => {
      if (seen.has(t.threadId)) return false;
      seen.add(t.threadId);
      return true;
    });
    unique.sort((a, b) =>
      new Date(b.updatedAt || b.createdAt || 0).getTime() -
      new Date(a.updatedAt || a.createdAt || 0).getTime()
    );
    setThreads(unique);
  };

  useImperativeHandle(ref, () => ({
    fetchThreads
  }));

  // Refresh on mount and when the window regains focus (multi-tab / multi-
  // device freshness). The previous 3-second polling timer is gone: the
  // sidebar lists only this user's own conversation rows, and every action
  // that mutates them (create, first-message title, rename, delete) already
  // triggers an explicit refresh — the timer's only real effect was a
  // steady idle re-fetch of the whole list.
  useEffect(() => {
    fetchThreads();
    const onFocus = () => fetchThreads();
    const onVisible = () => {
      if (document.visibilityState === "visible") fetchThreads();
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  // Commit an open rename when the click lands outside the editor input.
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (editingThreadId) {
        const target = e.target as HTMLElement;
        if (!target.classList.contains(styles.threadNameInput)) {
          updateThreadName(editingThreadId);
        }
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [editingThreadId, newThreadName]);

  const deleteThread = async (threadId: string, e: React.MouseEvent) => {
    e.stopPropagation();

    const threadToDelete = threads.find(thread => thread.threadId === threadId);
    if (!threadToDelete) return;

    const isActiveThread = threadId === currentThreadId;

    try {
      // 1. Send delete request to backend
      const response = await fetch(API_ENDPOINTS.deleteThread(), {
        credentials: "include",
        method: 'DELETE',
        body: JSON.stringify({
          threadId,
          isGroup: threadToDelete.isGroup
        }),
        headers: {
          'Content-Type': 'application/json',
        },
      });

      if (!response.ok) {
        throw new Error('Failed to delete thread');
      }

      // 2. Update local state after successful deletion
      setThreads((prevThreads) => {
        return prevThreads.filter((thread) => thread.threadId !== threadId);
      });

      // 3. If we deleted the active thread, show the welcome screen
      // If we deleted an inactive thread, keep the current view unchanged
      if (isActiveThread) {
        onThreadSelect(null, false);
      }

    } catch (error) {
      console.error('Failed to delete thread:', error);
    }
    await fetchThreads();
  };

  const handleCopyThreadId = () => {
    navigator.clipboard.writeText(showThreadInfo?.id || '');
    setIsModalOpen(false);
  };


  const updateThreadName = async (threadId: string) => {
    // Mirror the server's validation up front: trim, and treat an empty or
    // unchanged name as a cancelled edit rather than a request. Blurring an
    // empty input used to persist name: "" — now it just closes the editor.
    const name = newThreadName.trim();
    const current = threads.find((t) => t.threadId === threadId)?.name;
    if (!name || name === current) {
      setEditingThreadId(null);
      setNewThreadName("");
      return;
    }
    try {
      const response = await fetch(API_ENDPOINTS.updateThread(), {
        credentials: "include",
        method: 'PUT',
        body: JSON.stringify({ threadId, newName: name }),
        headers: {
          'Content-Type': 'application/json',
        },
      });
      if (response.ok) {
        setThreads((prevThreads) => {
          const updated = prevThreads.map((thread) =>
            thread.threadId === threadId
              ? { ...thread, name, updatedAt: new Date().toISOString() }
              : thread
          );
          updated.sort((a, b) =>
            new Date(b.updatedAt || b.createdAt || 0).getTime() -
            new Date(a.updatedAt || a.createdAt || 0).getTime()
          );
          return updated;
        });
        onThreadRenamed?.(threadId, name);
      } else {
        // Rejected (too long, thread gone, ...) — keep the old name on
        // screen rather than an optimistic one the server refused.
        console.error('Rename rejected with status', response.status);
      }
    } catch (error) {
      console.error('Failed to rename thread:', error);
    } finally {
      // Close the editor on every outcome, then reconcile with the server
      // (the fetch skips itself while an edit is open, so clear first).
      setEditingThreadId(null);
      setNewThreadName("");
      editingRef.current = null;
      fetchThreads();
    }
  };

  // Shows the Share Thread ID modal, and promotes a personal thread to a
  // group thread the first time it is shared.
  const toggleGroupStatus = async (threadId: string, isGroup: boolean, e: React.MouseEvent) => {
    e.stopPropagation();

    const thread = threads.find(t => t.threadId === threadId);
    if (thread) {
      setShowThreadInfo({ id: thread.threadId, name: thread.name, isGroup: thread.isGroup });
      setIsModalOpen(true);
    }

    if (!isGroup) {
      try {
        const response = await fetch(API_ENDPOINTS.updateThread(), {
          credentials: "include",
          method: 'PUT',
          body: JSON.stringify({ threadId, isGroup: true }),
          headers: {
            'Content-Type': 'application/json',
          },
        });
        if (response.ok) {
          setThreads((prevThreads) =>
            prevThreads.map((thread) =>
              thread.threadId === threadId ? { ...thread, isGroup: true } : thread
            )
          );
        }
      } catch (error) {
        console.error('Failed to toggle group status:', error);
      }
    }
  };


  const sortedUniqueThreads = useMemo(() => {
    const seen = new Set<string>();
    return threads
      .filter((t) => {
        if (seen.has(t.threadId)) return false;
        seen.add(t.threadId);
        return true;
      })
      .sort((a, b) =>
        new Date(b.updatedAt || b.createdAt || 0).getTime() -
        new Date(a.updatedAt || a.createdAt || 0).getTime()
      );
  }, [threads]);

  // Sidebar-owned view filters (mine/lab segmented control + search), then
  // contiguous day groups (the list is already newest-first).
  const groupedThreads = useMemo(() => {
    const q = (searchQuery ?? "").trim().toLowerCase();
    const visible = sortedUniqueThreads
      .filter((t) => (filter === "lab" ? t.isGroup : filter === "mine" ? !t.isGroup : true))
      .filter((t) => !q || (t.name || "").toLowerCase().includes(q));
    const groups: { label: string; threads: Thread[] }[] = [];
    for (const t of visible) {
      const label = dayLabel(t.updatedAt || t.createdAt);
      const last = groups[groups.length - 1];
      if (!last || last.label !== label) groups.push({ label, threads: [t] });
      else last.threads.push(t);
    }
    return groups;
  }, [sortedUniqueThreads, searchQuery, filter]);

  return (
    <div className={styles.list}>
      {groupedThreads.length === 0 && (
        <p className={styles.emptyNote}>
          {(searchQuery ?? "").trim()
            ? "No threads match your search."
            : filter === "lab"
              ? "No lab-shared threads yet. Share one from its ⋯ menu."
              : "No threads yet. Start one with New thread."}
        </p>
      )}
      {groupedThreads.map((group) => (
        <div key={group.label} className={styles.dayGroup}>
          <div className={styles.dayHeader}>{group.label}</div>
          {group.threads.map((thread) => (
            <div
              key={thread.threadId}
              className={`${styles.threadItem} ${thread.threadId === currentThreadId ? styles.active : ''}`}
              onClick={() => onThreadSelect(thread.threadId, thread.isGroup, thread.name)}
            >
              {editingThreadId === thread.threadId ? (
                <input
                  type="text"
                  value={newThreadName}
                  onChange={(e) => setNewThreadName(e.target.value)}
                  onBlur={() => updateThreadName(thread.threadId)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault();
                      updateThreadName(thread.threadId);
                    }
                  }}
                  className={styles.threadNameInput}
                  autoFocus
                />
              ) : (
                <>
                  <span className={styles.threadName}>{thread.name}</span>
                  {thread.isGroup && (
                    <Users
                      size={12}
                      strokeWidth={1.5}
                      className={styles.groupMark}
                      aria-label="Lab shared"
                    />
                  )}
                  <button
                    type="button"
                    className={styles.rowMenuBtn}
                    aria-label={`Thread menu for ${thread.name}`}
                    aria-haspopup="menu"
                    aria-expanded={menuFor === thread.threadId}
                    onClick={(e) => {
                      e.stopPropagation();
                      setMenuFor(menuFor === thread.threadId ? null : thread.threadId);
                    }}
                  >
                    <MoreHorizontal size={14} strokeWidth={1.5} />
                  </button>
                  {menuFor === thread.threadId && (
                    <>
                      <div
                        className={styles.menuBackdrop}
                        onMouseDown={(e) => {
                          e.stopPropagation();
                          setMenuFor(null);
                        }}
                      />
                      <div className={styles.rowMenu} role="menu">
                        <button
                          type="button"
                          role="menuitem"
                          className={styles.editBtn}
                          onClick={(e) => {
                            e.stopPropagation();
                            setMenuFor(null);
                            setEditingThreadId(thread.threadId);
                            setNewThreadName(thread.name);
                          }}
                        >
                          Rename
                        </button>
                        <button
                          type="button"
                          role="menuitem"
                          className={styles.groupBtn}
                          onClick={(e) => {
                            setMenuFor(null);
                            toggleGroupStatus(thread.threadId, thread.isGroup, e);
                          }}
                        >
                          {thread.isGroup ? "Thread ID & sharing" : "Share with lab"}
                        </button>
                        <button
                          type="button"
                          role="menuitem"
                          className={styles.deleteBtn}
                          onClick={(e) => {
                            setMenuFor(null);
                            deleteThread(thread.threadId, e);
                          }}
                        >
                          Delete
                        </button>
                      </div>
                    </>
                  )}
                </>
              )}
            </div>
          ))}
        </div>
      ))}
      <Modal show={isModalOpen} onClose={() => setIsModalOpen(false)}>
        <div>
          <h4>Share thread ID</h4>
          <p className={styles.shareHint}>
            Anyone in the lab can open this thread from Join team chat with this ID.
          </p>
          <div className={styles.shareContent}>
            <input
              type="text"
              value={showThreadInfo?.id || ''}
              readOnly
            />
            <button onClick={handleCopyThreadId}>
              Copy
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
});

ThreadList.displayName = 'ThreadList';

export default ThreadList;
