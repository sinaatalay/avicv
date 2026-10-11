import { AgentRunner, InMemoryAgentRunner, type AgentRunnerRunRequest, type AgentRunnerConnectRequest, type AgentRunnerStopRequest, type AgentRunnerIsRunningRequest } from "@copilotkit/runtime/v2";
import { map } from "rxjs";

import { reserveChat, type ChatLease } from "./admission";

/** CopilotKit's default runner has a process-global store: every path must namespace it. */
export class WorkspaceRunner extends AgentRunner {
  constructor(private readonly workspaceId: string, private readonly inner: AgentRunner = new InMemoryAgentRunner(), private readonly onFinished: () => Promise<void> = async () => {}, private readonly reservation?: ChatLease) { super(); }
  private key(threadId: string): string {
    if (!/^[a-zA-Z0-9_-]{1,128}$/.test(threadId)) throw new Error("Invalid conversation id");
    return `${this.workspaceId}:${threadId}`;
  }
  run(request: AgentRunnerRunRequest) {
    const key = this.key(request.threadId);
    const lease = this.reservation ?? reserveChat(this.workspaceId);
    if (!lease) throw new Error("Chat is busy. Please wait for the current reply.");
    lease.started = true;
    let released = false;
    const release = () => {
      if (released) return; released = true;
      lease.release();
      void this.onFinished().catch(() => {});
    };
    try {
      const result = this.inner.run({ ...request, threadId: key, input: { ...request.input, threadId: key } });
      // The inner runner is hot and keeps working after a tab disconnects. Release
      // capacity and partner resources when that run actually finishes.
      result.subscribe({ complete: release, error: release });
      return result.pipe(map((event) => "threadId" in event ? { ...event, threadId: request.threadId } : event));
    } catch (error) { release(); throw error; }
  }
  connect(request: AgentRunnerConnectRequest) {
    return this.inner.connect({ ...request, threadId: this.key(request.threadId) }).pipe(map((event) => "threadId" in event ? { ...event, threadId: request.threadId } : event));
  }
  isRunning(request: AgentRunnerIsRunningRequest) { return this.inner.isRunning({ threadId: this.key(request.threadId) }); }
  stop(request: AgentRunnerStopRequest) { return this.inner.stop({ ...request, threadId: this.key(request.threadId) }); }
}
