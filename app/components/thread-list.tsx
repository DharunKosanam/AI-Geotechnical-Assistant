import React, { useState, useEffect, forwardRef, useImperativeHandle } from "react";
import styles from "./thread-list.module.css";
import { API_ENDPOINTS } from "../config/api";

const Modal = ({ show, onClose, children }: { show: boolean; onClose: () => void; children: React.ReactNode }) => {
  if (!show) return null;

  return (
    <div className={styles.modalOverlay}>
      <div className={styles.modal}>
        <button className={styles.closeBtn} onClick={onClose}>
          &times;
        </button>
        {children}
      </div>
    </div>
  );
};

const TrashIcon = () => (
  <svg
    className={styles.fileDeleteIcon}
    xmlns="http://www.w3.org/2000/svg"
    viewBox="0 0 12 12"
    height="12"
    width="12"
    fill="#353740"
  >
    <path
      fillRule="evenodd"
      clipRule="evenodd"
      d="M5.15736 1.33332C4.8911 1.33332 4.65864 1.51361 4.59238 1.77149L4.4214 2.43693H7.58373L7.41275 1.77149C7.34649 1.51361 7.11402 1.33332 6.84777 1.33332H5.15736ZM8.78829 2.43693L8.54271 1.48115C8.34393 0.707516 7.64653 0.166656 6.84777 0.166656H5.15736C4.35859 0.166656 3.6612 0.707515 3.46241 1.48115L3.21683 2.43693H1.33333C1.01117 2.43693 0.75 2.6981 0.75 3.02026C0.75 3.34243 1.01117 3.6036 1.33333 3.6036H1.39207L2.10068 10.2683C2.19529 11.1582 2.94599 11.8333 3.84087 11.8333H8.15913C9.05401 11.8333 9.80471 11.1582 9.89932 10.2683L10.6079 3.6036H10.6667C10.9888 3.6036 11.25 3.34243 11.25 3.02026C11.25 2.6981 10.9888 2.43693 10.6667 2.43693H8.78829ZM9.43469 3.6036H2.56531L3.2608 10.145C3.29234 10.4416 3.54257 10.6667 3.84087 10.6667H8.15913C8.45743 10.6667 8.70766 10.4416 8.7392 10.145L9.43469 3.6036ZM4.83333 4.83332C5.1555 4.83332 5.41667 5.09449 5.41667 5.41666V8.33332C5.41667 8.65549 5.1555 8.91666 4.83333 8.91666C4.51117 8.91666 4.25 8.65549 4.25 8.33332V5.41666C4.25 5.09449 4.51117 4.83332 4.83333 4.83332ZM7.16667 4.83332C7.48883 4.83332 7.75 5.09449 7.75 5.41666V8.33332C7.75 8.65549 7.48883 8.91666 7.16667 8.91666C6.8445 8.91666 6.58333 8.65549 6.58333 8.33332V5.41666C6.58333 5.09449 6.8445 4.83332 7.16667 4.83332Z"
    />
  </svg>
);

const EditIcon = () => (
  <svg
    className={styles.fileEditIcon}
    xmlns="http://www.w3.org/2000/svg"
    viewBox="0 0 24 24"
    height="12"
    width="12"
    fill="#353740"
  >
    <path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34c-.39-.39-1.02-.39-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/>
  </svg>
);

const GroupIcon = () => (
  <svg 
    xmlns="http://www.w3.org/2000/svg" 
    width="12" 
    height="12" 
    viewBox="0 -960 960 960"
    >
      <path d="M40-160v-112q0-34 17.5-62.5T104-378q62-31 126-46.5T360-440t130 15.5T616-378q29 15 46.5 43.5T680-272v112zm720 0v-120q0-44-24.5-84.5T666-434q51 6 96 20.5t84 35.5q36 20 55 44.5t19 53.5v120zM360-480q-66 0-113-47t-47-113 47-113 113-47 113 47 47 113-47 113-113 47m400-160q0 66-47 113t-113 47q-11 0-28-2.5t-28-5.5q27-32 41.5-71t14.5-81-14.5-81-41.5-71q14-5 28-6.5t28-1.5q66 0 113 47t47 113M120-240h480v-32q0-11-5.5-20T580-306q-54-27-109-40.5T360-360t-111 13.5T140-306q-9 5-14.5 14t-5.5 20zm240-320q33 0 56.5-23.5T440-640t-23.5-56.5T360-720t-56.5 23.5T280-640t23.5 56.5T360-560m0-80"/>
  </svg>
);

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
  onThreadSelect: (threadId: string | null, isGroup: boolean) => void;
}

const ThreadList = forwardRef<any, ThreadListProps>(({ currentThreadId, onThreadSelect }, ref) => {
  const [threads, setThreads] = useState<Thread[]>([]);
  const [editingThreadId, setEditingThreadId] = useState<string | null>(null);
  const [newThreadName, setNewThreadName] = useState<string>("");
  const [showThreadInfo, setShowThreadInfo] = useState<{ id: string, name: string, isGroup: boolean } | null>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);

  const fetchThreads = async () => {
    const response = await fetch(API_ENDPOINTS.getThreadHistory());
    const data = await response.json();
    // 确保访问 threads 数组属性并进行反转
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

  useEffect(() => {
    fetchThreads();
    const interval = setInterval(fetchThreads, 3000);
    return () => clearInterval(interval);
  }, []);

  // 在其他 hooks 后面添加
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (editingThreadId) {
        const target = e.target as HTMLElement;
        // 如果点击的不是输入框，就更新线程名称并关闭编辑状态
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
    setIsModalOpen(false); // 复制后关闭弹窗
  };


  const updateThreadName = async (threadId: string) => {
    try {
      const response = await fetch(API_ENDPOINTS.updateThread(), {
        method: 'PUT',
        body: JSON.stringify({ threadId, newName: newThreadName }),
        headers: {
          'Content-Type': 'application/json',
        },
      });
      if (response.ok) {
        setThreads((prevThreads) => {
          const updated = prevThreads.map((thread) =>
            thread.threadId === threadId
              ? { ...thread, name: newThreadName, updatedAt: new Date().toISOString() }
              : thread
          );
          updated.sort((a, b) =>
            new Date(b.updatedAt || b.createdAt || 0).getTime() -
            new Date(a.updatedAt || a.createdAt || 0).getTime()
          );
          return updated;
        });
        setEditingThreadId(null);
        setNewThreadName("");
      }
    } catch (error) {
      console.error('更新线程名称失败:', error);
    }
  };

// 修改 toggleGroupStatus 函数
const toggleGroupStatus = async (threadId: string, isGroup: boolean, e: React.MouseEvent) => {
  e.stopPropagation();
  
  // 显示 threadId 信息
  const thread = threads.find(t => t.threadId === threadId);
  if (thread) {
    setShowThreadInfo({ id: thread.threadId, name: thread.name, isGroup: thread.isGroup });
    setIsModalOpen(true);
  }

  // 如果不是群组，则设置为群组
  if (!isGroup) {
    try {
      const response = await fetch(API_ENDPOINTS.updateThread(), {
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
      console.error('切换群组状态失败:', error);
    }
  }
};


  return (
    <div className={styles.sidebar}>
      <h3>Conversation History</h3>
      {threads.map((thread) => (
        <div
          key={thread.threadId}
          className={`${styles.threadItem} ${thread.threadId === currentThreadId ? styles.active : ''}`}
          onClick={() => onThreadSelect(thread.threadId, thread.isGroup)} // 传递 isGroup
        >
          {editingThreadId === thread.threadId ? (
            <input
              type="text"
              value={newThreadName}
              onChange={(e) => setNewThreadName(e.target.value)}
              onBlur={() => updateThreadName(thread.threadId)}
              onKeyDown={(e) => {  // 添加这个事件处理
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
              <span>{thread.name}</span>
              <button
                className={styles.editBtn}
                onClick={(e) => {
                  e.stopPropagation();
                  setEditingThreadId(thread.threadId);
                  setNewThreadName(thread.name);
                }}
              >
                <EditIcon />
              </button>
              <button
                className={`${styles.groupBtn} ${thread.isGroup ? styles.active : ''}`}
                onClick={(e) => toggleGroupStatus(thread.threadId, thread.isGroup, e)}
              >
                <GroupIcon />
              </button>
            </>
          )}
          <button 
            className={styles.deleteBtn}
            onClick={(e) => deleteThread(thread.threadId, e)}
          >
            <TrashIcon />
          </button>
        </div>
      ))}
      <Modal show={isModalOpen} onClose={() => setIsModalOpen(false)}>
        <div>
          <h4>Share Thread ID</h4>
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