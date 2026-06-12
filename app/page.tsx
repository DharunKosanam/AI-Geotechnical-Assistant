"use client";
import React from "react";
import Chat from "./components/chat";
import Header from "./components/Header";
import styles from "./page.module.css";

const FileSearchPage = () => {
  return (
    <main className={styles.main}>
      <Header />
      <div className={styles.container}>
        <div className={styles.chatContainer}>
          <div className={styles.chat}>
            <Chat />
          </div>
        </div>
      </div>
    </main>
  );
};

export default FileSearchPage;