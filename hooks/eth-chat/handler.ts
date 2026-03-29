import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

type HookEvent = {
  type: string;
  action: string;
  messages: string[];
  context: {
    body?: string;
    bodyForAgent?: string;
    content?: string;
    from?: string;
    channelId?: string;
    messageId?: string;
  };
};

const handlerDir = dirname(fileURLToPath(import.meta.url));
const projectDir = resolve(handlerDir, "..", "..");
const scriptPath = resolve(projectDir, "scripts", "eth_watcher.py");
const localConfigPath = resolve(projectDir, "config.local.json");
const configPath = existsSync(localConfigPath) ? localConfigPath : resolve(projectDir, "config.json");
const statePath = resolve(projectDir, "state", "runtime.json");

const normalize = (value: unknown): string => String(value ?? "").trim().toLowerCase();

const isLikelyTargetSender = (sender: string): boolean => {
  try {
    const config = JSON.parse(readFileSync(configPath, "utf8"));
    const target = normalize(config?.notification?.target);
    const current = normalize(sender);
    if (!target || !current) {
      return false;
    }
    return target.includes(current) || current.includes(target);
  } catch {
    return false;
  }
};

const handler = async (event: HookEvent) => {
  if (event.type !== "message" || event.action !== "preprocessed") {
    return;
  }

  const channelId = normalize(event.context.channelId);
  if (channelId !== "imessage") {
    return;
  }

  const sender = String(event.context.from ?? "").trim();
  if (!isLikelyTargetSender(sender)) {
    return;
  }

  const message =
    String(event.context.bodyForAgent ?? "").trim() ||
    String(event.context.body ?? "").trim() ||
    String(event.context.content ?? "").trim();
  if (!message) {
    return;
  }

  try {
    const raw = execFileSync(
      "python3",
      [
        scriptPath,
        "--config",
        configPath,
        "--state",
        statePath,
        "chat-query",
        "--message",
        message,
        "--sender",
        sender,
        "--message-id",
        String(event.context.messageId ?? ""),
      ],
      {
        encoding: "utf8",
        cwd: projectDir,
        timeout: 60000,
      }
    ).trim();
    if (!raw) {
      return;
    }

    const payload = JSON.parse(raw) as { matched?: boolean; reply?: string };
    if (!payload.matched || !payload.reply) {
      return;
    }
    event.messages.push(payload.reply);
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    console.error(`[eth-chat] reply failed: ${detail}`);
  }
};

export default handler;
