import { expect, test } from "bun:test";
import { AgentRunner, type AgentRunnerRunRequest, type AgentRunnerConnectRequest, type AgentRunnerStopRequest, type AgentRunnerIsRunningRequest } from "@copilotkit/runtime/v2";
import type { BaseEvent } from "@ag-ui/client";
import { ReplaySubject, of } from "rxjs";
import { WorkspaceRunner } from "./scoped-runner";
class FakeRunner extends AgentRunner {
  streams = new Map<string, ReplaySubject<BaseEvent>>();
  run(request: AgentRunnerRunRequest) { const stream = new ReplaySubject<BaseEvent>(); this.streams.set(request.threadId, stream); stream.next({ type: "RUN_STARTED", threadId: request.input.threadId, runId: "run" } as BaseEvent); return stream; }
  connect(request: AgentRunnerConnectRequest) { return this.streams.get(request.threadId) ?? of(); }
  async isRunning(request: AgentRunnerIsRunningRequest) { return this.streams.has(request.threadId); }
  async stop(request: AgentRunnerStopRequest) { const stream = this.streams.get(request.threadId); stream?.complete(); this.streams.delete(request.threadId); return Boolean(stream); }
}
const request = (threadId: string) => ({ threadId, input: { threadId } }) as AgentRunnerRunRequest;
test("visitors cannot connect to or stop each other's conversation even with the same client id", async () => {
  const inner = new FakeRunner(); const alice = new WorkspaceRunner("alice", inner); const bob = new WorkspaceRunner("bob", inner);
  const events: BaseEvent[] = []; alice.run(request("same-id")).subscribe((event) => events.push(event));
  expect((events[0] as any).threadId).toBe("same-id");
  expect(await bob.isRunning({ threadId: "same-id" })).toBe(false);
  expect(await bob.stop({ threadId: "same-id" })).toBe(false);
  const foreign: BaseEvent[] = []; bob.connect({ threadId: "same-id" }).subscribe((event) => foreign.push(event)); expect(foreign).toHaveLength(0);
  expect(await alice.isRunning({ threadId: "same-id" })).toBe(true);
  expect(() => bob.connect({ threadId: "alice:same-id" })).toThrow("Invalid");
  await alice.stop({ threadId: "same-id" });
});
test("disconnect does not release capacity while the model is still running", async () => {
  const inner = new FakeRunner(); let closed = 0;
  const runner = new WorkspaceRunner("capacity-owner", inner, async () => { closed++; });
  const subscription = runner.run(request("one")).subscribe(); subscription.unsubscribe();
  expect(() => runner.run(request("two"))).toThrow("busy");
  await runner.stop({ threadId: "one" }); expect(closed).toBe(1);
  runner.run(request("two")).subscribe(); await runner.stop({ threadId: "two" }); expect(closed).toBe(2);
});
