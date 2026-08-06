"use client";

import React from "react";
import Header from "../components/Header";
import KbUpload from "../components/kb-upload";
import { AuthProvider } from "../lib/auth-context";
import AuthGuard from "../components/auth-guard";
import styles from "../page.module.css";

const KnowledgeBasePage = () => {
  return (
    <AuthProvider>
      <AuthGuard>
        <main className={styles.main}>
          <Header />
          <KbUpload />
        </main>
      </AuthGuard>
    </AuthProvider>
  );
};

export default KnowledgeBasePage;
