import { expect, test } from "bun:test";
import { DARWIN_TOOLS, createDarwinProvider } from "./darwin";

const tool = (name: string) => ({ description: name, inputSchema: {}, execute: async () => name });

test("only search is exposed from Darwin's tool list", async () => {
  const provider = createDarwinProvider("http://darwin.test", {
    createClient: async () =>
      ({
        tools: async () => ({ search: tool("search"), act: tool("act"), show_darwin_view: tool("show_darwin_view") }),
        close: async () => {},
      }) as never,
  });
  expect(Object.keys(await provider.tools())).toEqual([...DARWIN_TOOLS]);
});

test("a throwing client yields {} and the next call retries creation", async () => {
  let created = 0;
  let closed = 0;
  const provider = createDarwinProvider("http://darwin.test", {
    createClient: async () => {
      created++;
      const n = created;
      return {
        tools: async () => {
          if (n === 1) throw new Error("down");
          return { search: tool("search") };
        },
        close: async () => void closed++,
      } as never;
    },
  });
  const quiet = console.error;
  console.error = () => {};
  try {
    expect(await provider.tools()).toEqual({});
  } finally {
    console.error = quiet;
  }
  expect(closed).toBe(1);
  expect(Object.keys(await provider.tools())).toEqual(["search"]);
  await provider.tools();
  expect(created).toBe(2);
});

test("a failing createClient yields {}", async () => {
  const provider = createDarwinProvider("http://darwin.test", {
    createClient: async () => {
      throw new Error("refused");
    },
  });
  const quiet = console.error;
  console.error = () => {};
  try {
    expect(await provider.tools()).toEqual({});
  } finally {
    console.error = quiet;
  }
});
