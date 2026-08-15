"use client";

/**
 * Chat sidebar (dark redesign). Extracted from chat.tsx; all thread/session
 * handlers stay owned by chat.tsx and arrive as props — this component only
 * adds presentation state (collapse, search text, mine/lab filter).
 *
 * The mockup's footer (model name, on-premise status, library doc count) is
 * intentionally absent: no endpoint exposes any of those fields, so per the
 * rollout rules the block is omitted rather than invented.
 */
import React, { useState } from "react";
import { Layers, PanelLeftClose, Plus, Search, Users } from "lucide-react";
import ThreadList from "./thread-list";
import s from "./sidebar.module.css";

type SidebarProps = {
  threadListRef: React.MutableRefObject<any>;
  currentThreadId: string | null;
  onThreadSelect: (threadId: string | null, isGroup: boolean, name?: string) => void;
  onNewChat: () => void;
  onJoinTeam: () => void;
  collapsed?: boolean;
  onToggleCollapse?: () => void;
  onThreadRenamed?: (threadId: string, name: string) => void;
};

const Sidebar = ({
  threadListRef,
  currentThreadId,
  onThreadSelect,
  onNewChat,
  onJoinTeam,
  collapsed,
  onToggleCollapse,
  onThreadRenamed,
}: SidebarProps) => {
  const [searchOpen, setSearchOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<"mine" | "lab">("mine");

  const closeSearch = () => {
    setSearchOpen(false);
    setSearch("");
  };

  return (
    <aside className={s.sidebar} data-collapsed={collapsed || undefined}>
      <div className={s.inner}>
        <div className={s.brandRow}>
          <div className={s.brand}>
            <Layers size={16} strokeWidth={1.5} />
            <span className={s.brandName}>GeoTech AI</span>
          </div>
          {onToggleCollapse && (
            <button
              type="button"
              className={s.iconBtn}
              onClick={onToggleCollapse}
              aria-label="Collapse sidebar"
              title="Collapse sidebar"
            >
              <PanelLeftClose size={16} strokeWidth={1.5} />
            </button>
          )}
        </div>

        <div className={s.actionsRow}>
          <button type="button" className={s.newThreadBtn} onClick={onNewChat}>
            <Plus size={14} strokeWidth={1.5} />
            New thread
          </button>
          <button
            type="button"
            className={`${s.iconBtn} ${searchOpen ? s.iconBtnActive : ""}`}
            onClick={() => (searchOpen ? closeSearch() : setSearchOpen(true))}
            aria-label="Search threads"
            aria-expanded={searchOpen}
            title="Search threads"
          >
            <Search size={14} strokeWidth={1.5} />
          </button>
        </div>

        {searchOpen && (
          <div className={s.searchRow}>
            <input
              className={s.searchInput}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") closeSearch();
              }}
              placeholder="Search threads"
              autoFocus
            />
          </div>
        )}

        <div className={s.filterRow}>
          <div className={s.segmented} role="group" aria-label="Thread filter">
            <button
              type="button"
              className={`${s.segment} ${filter === "mine" ? s.segmentActive : ""}`}
              aria-pressed={filter === "mine"}
              onClick={() => setFilter("mine")}
            >
              Mine
            </button>
            <button
              type="button"
              className={`${s.segment} ${filter === "lab" ? s.segmentActive : ""}`}
              aria-pressed={filter === "lab"}
              onClick={() => setFilter("lab")}
            >
              Lab shared
            </button>
          </div>
        </div>

        <ThreadList
          ref={threadListRef}
          currentThreadId={currentThreadId}
          onThreadSelect={onThreadSelect}
          searchQuery={search}
          filter={filter}
          onThreadRenamed={onThreadRenamed}
        />

        <div className={s.footer}>
          <button type="button" className={s.footerBtn} onClick={onJoinTeam}>
            <Users size={14} strokeWidth={1.5} />
            Join team chat
          </button>
        </div>
      </div>
    </aside>
  );
};

export default Sidebar;
