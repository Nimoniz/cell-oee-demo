"use client";

import { useEffect, useRef, useState } from "react";

import { getLive } from "../api/client";
import type { LiveOut } from "../api/types";
import { getRuntimeConfig } from "../runtimeConfig";

/**
 * The live cell state, kept in sync over `/ws/live`.
 *
 * `now` is the stream's own "now" — the latest simulated timestamp received, kept monotonic
 * across the initial REST snapshot and every WebSocket message — never the browser's wall
 * clock, and never re-derived by polling the REST endpoint again.
 */

export type StreamStatus = "connecting" | "live" | "stalled" | "disconnected";

const STALL_MS = 5_000; // no message for this long (wall clock): the feed looks dead
const MAX_BACKOFF_MS = 10_000;

export interface UseLiveResult {
  data: LiveOut | null;
  status: StreamStatus;
}

export function useLive(): UseLiveResult {
  const [data, setData] = useState<LiveOut | null>(null);
  const [status, setStatus] = useState<StreamStatus>("connecting");

  const nowRef = useRef<string | null>(null);
  const lastMessageAt = useRef<number>(0);

  useEffect(() => {
    let cancelled = false;
    let ws: WebSocket | null = null;
    let backoff = 500;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;

    function applyMessage(msg: LiveOut): void {
      if (msg.now !== null && (nowRef.current === null || msg.now > nowRef.current)) {
        nowRef.current = msg.now;
      }
      lastMessageAt.current = Date.now();
      setData({ ...msg, now: nowRef.current });
      setStatus("live");
    }

    function connect(): void {
      const { wsUrl } = getRuntimeConfig();
      const socket = new WebSocket(`${wsUrl}/ws/live`);
      ws = socket;
      socket.onopen = () => {
        backoff = 500;
      };
      socket.onmessage = (event) => {
        applyMessage(JSON.parse(event.data as string) as LiveOut);
      };
      socket.onclose = () => {
        if (cancelled) return;
        setStatus("disconnected");
        reconnectTimer = setTimeout(connect, backoff);
        backoff = Math.min(backoff * 2, MAX_BACKOFF_MS);
      };
      socket.onerror = () => socket.close();
    }

    // An initial REST snapshot so the screen has something to show before the socket handshake
    // completes; the WebSocket then takes over as the sole source of live updates.
    getLive()
      .then((snapshot) => {
        if (!cancelled) applyMessage(snapshot);
      })
      .catch(() => {
        // the WebSocket will supply the first snapshot instead
      });
    connect();

    const stallCheck = setInterval(() => {
      if (lastMessageAt.current && Date.now() - lastMessageAt.current > STALL_MS) {
        setStatus((s) => (s === "live" ? "stalled" : s));
      }
    }, 1_000);

    return () => {
      cancelled = true;
      clearTimeout(reconnectTimer);
      clearInterval(stallCheck);
      ws?.close();
    };
  }, []);

  return { data, status };
}
