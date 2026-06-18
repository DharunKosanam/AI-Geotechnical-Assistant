"use client";
import React from "react";
import Chat from "./components/chat";
import Header from "./components/Header";
import styles from "./page.module.css";
import { AuthProvider } from "./lib/auth-context";
import AuthGuard from "./components/auth-guard";

const FileSearchPage = () => {
  return (
    <AuthProvider>
      <AuthGuard>
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
      </AuthGuard>
    </AuthProvider>
  );
};

export default FileSearchPage;