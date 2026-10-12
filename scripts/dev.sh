#!/usr/bin/env bash
# Runs the whole stack (SvelteKit, FastAPI and the job sync) and stops all of it when you quit.
#
#   scripts/dev.sh                          # or: bun run dev
#   WEB_PORT=5273 API_PORT=8100 scripts/dev.sh
#
# Each service runs in its own process group, so stopping one also stops everything it
# started: uvicorn's reloader and worker, Vite's esbuild, the Python process behind `uv run`.
# Ctrl-C, closing the terminal, `kill`, or any service exiting on its own stops all of them.
# Written for the bash 3.2 that ships with macOS.

set -eo pipefail
set -m # job control: every background job gets its own process group

cd "$(dirname "$0")/.."
root=$(pwd)
web_port=${WEB_PORT:-5173}
api_port=${API_PORT:-8000}
agent_port=${AGENT_PORT:-4000}
app_origin=${APP_ORIGIN:-http://localhost:$web_port}

if [ -t 1 ]; then
	bold=$'\e[1m' reset=$'\e[0m' red=$'\e[31m' cyan=$'\e[36m' magenta=$'\e[35m' yellow=$'\e[33m'
	export FORCE_COLOR=1
else
	bold='' reset='' red='' cyan='' magenta='' yellow=''
fi

# Never fails, so a closed terminal can't interrupt the cleanup
say() { printf '%s\n' "${bold}dev ${reset} │ $*" 2>/dev/null || true; }

# Stops a process group: politely first, then forcefully after 5 seconds
stop_group() {
	kill -TERM -- "-$1" 2>/dev/null || return 0
	for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25; do
		kill -0 -- "-$1" 2>/dev/null || return 0
		sleep 0.2
	done
	kill -KILL -- "-$1" 2>/dev/null || true
}

# Process groups of dev servers from this repo whose run is gone: the group's leader has exited
# or been orphaned. That's what an earlier run leaves behind if it was killed with SIGKILL.
# A stack running in another terminal still has its leader, so it's left alone.
leftover_groups() {
	local pid cwd group parent
	for pid in $(pgrep -f 'vite dev|fastapi dev|jobs-ingest --loop'); do
		cwd=$(lsof -a -nP -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')
		case "$cwd" in "$root" | "$root"/*) ;; *) continue ;; esac
		group=$(ps -o pgid= -p "$pid" | tr -d ' ' || true)
		[ -n "$group" ] || continue # it exited meanwhile
		parent=$(ps -o ppid= -p "$group" 2>/dev/null | tr -d ' ' || true)
		if [ -z "$parent" ] || [ "$parent" = 1 ]; then echo "$group"; fi
	done | sort -u
}

pids=()
names=()

# Starts a service in the background, prefixing each line of its output with its name
start() {
	local name=$1 color=$2
	shift 2
	(
		"$@" </dev/null 2>&1 | awk -v prefix="$color$(printf '%-4s' "$name")$reset │ " \
			'{ print prefix $0; fflush() }'
	) &
	pids+=("$!") # with job control, the job's PID is also its process group ID
	names+=("$name")
}

stopping=''
stop_all() {
	set +e # the cleanup must run to the end
	[ -n "$stopping" ] && return
	stopping=1
	trap '' INT TERM HUP
	[ ${#pids[@]} -eq 0 ] && return
	say "stopping…"
	local pid
	for pid in "${pids[@]}"; do stop_group "$pid" & done
	wait
	say "stopped"
}
trap stop_all EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exec >/dev/null 2>&1; exit 129' HUP # the terminal is gone, so stop writing to it

command -v bun >/dev/null || { say "${red}bun is not installed: https://bun.com${reset}"; exit 1; }
command -v uv >/dev/null || { say "${red}uv is not installed: https://docs.astral.sh/uv/${reset}"; exit 1; }
[ -f backend/.env ] ||
	say "${yellow}backend/.env is missing, so uploads will fail: cp backend/.env.example backend/.env${reset}"

for group in $(leftover_groups); do
	service=$(ps -o command= -g "$group" | grep -m1 -E 'vite dev|fastapi dev|jobs-ingest --loop' || true)
	say "stopping leftovers of an earlier run: ${service##*/}"
	stop_group "$group"
done
for port in "$web_port" "$api_port" "$agent_port"; do
	pid=$(lsof -nP -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | head -1 || true) # lsof fails if free
	if [ -n "$pid" ]; then
		say "${red}port $port is in use by: $(ps -o command= -p "$pid" | cut -c1-100)${reset}"
		exit 1
	fi
done

say "installing dependencies"
bun install >/dev/null
uv sync --quiet

start web "$cyan" env APP_ORIGIN="$app_origin" API_URL="http://127.0.0.1:$api_port" AGENT_URL="http://127.0.0.1:$agent_port" \
	bun --cwd=frontend run dev --port "$web_port" --strictPort
start api "$magenta" env APP_ORIGIN="$app_origin" uv run --directory backend fastapi dev --port "$api_port"
agent_env=()
[ ! -f backend/.env ] || agent_env+=(--env-file="$root/backend/.env")
[ ! -f agent/.env ] || agent_env+=(--env-file="$root/agent/.env")
start chat "$cyan" env APP_ORIGIN="$app_origin" API_URL="http://127.0.0.1:$api_port" PORT="$agent_port" \
	bun "${agent_env[@]}" --cwd=agent run dev
start jobs "$yellow" uv run --directory backend jobs-ingest --loop

# Wait for the servers, then keep watching: if any service exits, stop the rest
ready=''
while :; do
	for i in "${!pids[@]}"; do
		if ! kill -0 "${pids[$i]}" 2>/dev/null; then
			say "${red}${names[$i]} exited, so everything stops${reset}"
			exit 1
		fi
	done
	if [ -z "$ready" ] &&
		curl -fsS -o /dev/null "http://127.0.0.1:$api_port/health" 2>/dev/null &&
		curl -fsS -o /dev/null "http://127.0.0.1:$agent_port/health" 2>/dev/null &&
		curl -fsS -o /dev/null "http://localhost:$web_port/" 2>/dev/null; then
		ready=1
		say "${bold}ready${reset}: app $app_origin · MCP $app_origin/mcp · API docs http://127.0.0.1:$api_port/docs"
		say "Ctrl-C stops everything"
	fi
	sleep 0.5 &
	wait $! 2>/dev/null || true # unlike a bare sleep, wait lets traps run at once
done
