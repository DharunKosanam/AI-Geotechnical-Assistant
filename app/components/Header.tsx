import React from "react";
import { Layers, User } from "lucide-react";
import styles from "./header.module.css";

const Header = () => {
  return (
    <header className={styles.header}>
      <div className={styles.brand}>
        <Layers size={20} />
        <span className={styles.appName}>GeoTech AI</span>
      </div>
      {/* Placeholder for future auth — intentionally a no-op. */}
      <button type="button" className={styles.avatar} aria-label="Account">
        <User size={16} />
      </button>
    </header>
  );
};

export default Header;
