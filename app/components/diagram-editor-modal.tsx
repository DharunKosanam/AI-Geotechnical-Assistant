"use client";

import React, { useEffect, useRef, useState } from "react";
import styles from "./chat.module.css";

// draw.io embed endpoint (DIAGRAM_EDITOR_ENABLED). proto=json switches the
// iframe to the JSON postMessage protocol; modified=unsavedChanges makes its
// own exit prompt track dirty state so users aren't asked about work they
// already saved.
const EMBED_ORIGIN = "https://embed.diagrams.net";
const EMBED_URL = `${EMBED_ORIGIN}/?embed=1&ui=atlas&spin=1&proto=json&modified=unsavedChanges`;

// If {event:'init'} never arrives (offline, extension blocking the frame, a
// diagrams.net outage) the modal must not sit as a blank frame forever.
const INIT_TIMEOUT_MS = 10_000;

type DiagramEditorModalProps = {
  // Fired once per completed save round-trip: the PNG data URI (draw.io's
  // xmlpng format — a PNG with the source XML embedded) plus the raw diagram
  // XML. The parent owns what happens next (Phase 1: console only).
  onExport: (pngDataUri: string, xml: string) => void;
  // User exited the editor (or export finished): unmount, no side effects.
  onClose: () => void;
  // Init timeout — the parent closes the modal and surfaces the message.
  onError: (message: string) => void;
};

// Full-screen draw.io editor. Handshake (all messages are JSON strings, and
// every inbound message is origin-checked against embed.diagrams.net):
//   {event:'init'}   -> {action:'load', xml:'', autosave:0}
//   {event:'save'}   -> {action:'export', format:'xmlpng', xml, spin}
//   {event:'export'} -> onExport(data URI, xml); parent closes
//   {event:'exit'}   -> onClose(); everything discarded
const DiagramEditorModal = ({ onExport, onClose, onError }: DiagramEditorModalProps) => {
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  // The iframe renders only AFTER the message listener is registered (first
  // effect flips this), so a fast-loading frame can never emit 'init' into
  // the void.
  const [listenerReady, setListenerReady] = useState(false);
  const initReceivedRef = useRef(false);
  // The save event carries the XML; the export event is not guaranteed to
  // echo it back, so keep the last-saved XML as the extraction source.
  const lastSavedXmlRef = useRef("");

  // The listener is registered once; these refs keep it reading the latest
  // callbacks without re-registering (which would race a mid-flight message).
  const onExportRef = useRef(onExport);
  const onCloseRef = useRef(onClose);
  const onErrorRef = useRef(onError);
  onExportRef.current = onExport;
  onCloseRef.current = onClose;
  onErrorRef.current = onError;

  useEffect(() => {
    const post = (payload: Record<string, unknown>) => {
      iframeRef.current?.contentWindow?.postMessage(JSON.stringify(payload), EMBED_ORIGIN);
    };

    const handleMessage = (event: MessageEvent) => {
      // Reject anything not from the embedded editor: wrong origin, or a
      // same-origin message from some other window (extensions, devtools).
      if (event.origin !== EMBED_ORIGIN) return;
      const frame = iframeRef.current;
      if (frame && event.source !== frame.contentWindow) return;

      let msg: { event?: string; xml?: string; data?: string };
      try {
        msg = JSON.parse(String(event.data));
      } catch {
        return; // not a protocol message
      }

      switch (msg.event) {
        case "init":
          initReceivedRef.current = true;
          post({ action: "load", xml: "", autosave: 0 });
          break;
        case "save":
          lastSavedXmlRef.current = msg.xml ?? "";
          post({
            action: "export",
            format: "xmlpng",
            xml: msg.xml,
            spin: "Saving diagram",
          });
          break;
        case "export":
          onExportRef.current(msg.data ?? "", msg.xml ?? lastSavedXmlRef.current);
          break;
        case "exit":
          onCloseRef.current();
          break;
        default:
          break; // other protocol events (configure, autosave, ...) are unused
      }
    };

    window.addEventListener("message", handleMessage);
    setListenerReady(true);
    return () => window.removeEventListener("message", handleMessage);
  }, []);

  // Init watchdog, armed only once the iframe is actually mounted.
  useEffect(() => {
    if (!listenerReady) return;
    const timer = setTimeout(() => {
      if (!initReceivedRef.current) {
        onErrorRef.current(
          "The diagram editor didn't load (no response from embed.diagrams.net " +
            "after 10 seconds). Check your connection and try again.",
        );
      }
    }, INIT_TIMEOUT_MS);
    return () => clearTimeout(timer);
  }, [listenerReady]);

  return (
    <div className={styles.diagramModalOverlay} role="dialog" aria-modal="true" aria-label="Draw a diagram">
      {listenerReady && (
        <iframe
          ref={iframeRef}
          src={EMBED_URL}
          className={styles.diagramModalFrame}
          title="draw.io diagram editor"
        />
      )}
    </div>
  );
};

export default DiagramEditorModal;
