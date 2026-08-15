"use client";

/**
 * Chat sidebar. Extracted verbatim from chat.tsx (the .leftPanel region) so
 * the dark-redesign restyle diffs cleanly against a pure move; all handlers
 * stay owned by chat.tsx and arrive as props.
 */
import React from "react";
import { SquarePen, Users } from "lucide-react";
import ThreadList from "./thread-list";
import SidebarAccount from "./sidebar-account";
import styles from "./chat.module.css";

type SidebarProps = {
  threadListRef: React.MutableRefObject<any>;
  currentThreadId: string | null;
  onThreadSelect: (threadId: string | null, isGroup: boolean) => void;
  onNewChat: () => void;
  onJoinTeam: () => void;
};

const Sidebar = ({
  threadListRef,
  currentThreadId,
  onThreadSelect,
  onNewChat,
  onJoinTeam,
}: SidebarProps) => {
  return (
    <div className={styles.leftPanel}>
      <button
        type="button"
        onClick={onNewChat}
        className={styles.newChatBtn}
      >
        <SquarePen size={16} />
        New Chat
      </button>
      <ThreadList
        ref={threadListRef}
        currentThreadId={currentThreadId}
        onThreadSelect={onThreadSelect}
      />
      <button
        type="button"
        onClick={onJoinTeam}
        className={styles.joinTeamBtn}
      >
        <Users size={16} />
        Join Team Chat
      </button>
      <SidebarAccount />
    </div>
  );
};

export default Sidebar;
