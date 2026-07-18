from __future__ import annotations

import json
from string import Template

from vibe.cli.mcp_apps.models import MCPAppInitialState

_HOST_PAGE = Template(
    """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MCP App</title>
  <style>
    :root { color-scheme: light dark; font-family: system-ui, sans-serif; }
    body { margin: 0; background: Canvas; color: CanvasText; }
    #error { display: none; padding: 12px 16px; background: #8b1a1a; color: white; }
    #app { width: 100%; height: 100vh; border: 0; display: block; }
  </style>
</head>
<body>
  <div id="error" role="alert" aria-live="polite"></div>
  <iframe id="app" title="MCP App" sandbox="allow-scripts"></iframe>
  <script>
    const sessionToken = $token;
    const appHtml = $app_html;
    const initialState = $initial_state;
    const frame = document.getElementById("app");
    const errorBox = document.getElementById("error");

    function showError(message) {
      errorBox.textContent = message;
      errorBox.style.display = "block";
    }

    async function forward(message) {
      let payload;
      try {
        const response = await fetch("/api/message", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({...message, token: sessionToken}),
        });
        payload = await response.json();
      } catch (error) {
        payload = {
          type: "error",
          request_id: message.request_id || "",
          error: "Host communication failed: " + String(error),
        };
      }

      if (payload.type === "error") {
        showError(payload.error);
      }
      frame.contentWindow.postMessage(payload, "*");
    }

    window.addEventListener("message", (event) => {
      if (event.source !== frame.contentWindow) return;
      const message = event.data;
      if (!message || typeof message !== "object") return;
      if (message.type !== "call_tool" && message.type !== "send_user_message") return;
      void forward(message);
    });

    frame.addEventListener("load", () => {
      frame.contentWindow.postMessage({type: "initial_state", state: initialState}, "*");
    });
    frame.srcdoc = appHtml;
  </script>
</body>
</html>
"""
)


def render_host_page(
    *, token: str, app_html: str, initial_state: MCPAppInitialState
) -> str:
    return _HOST_PAGE.substitute(
        token=_script_json(token),
        app_html=_script_json(app_html),
        initial_state=_script_json(initial_state.model_dump(mode="json")),
    )


def _script_json(value: object) -> str:
    return (
        json
        .dumps(value, ensure_ascii=False, separators=(",", ":"))
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
