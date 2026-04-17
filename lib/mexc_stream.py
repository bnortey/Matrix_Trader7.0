import json
import threading
import time
from collections.abc import Callable

from websocket import WebSocketApp

WS_URL = "wss://contract.mexc.com/edge"
_MAX_RETRIES = 3
_RETRY_DELAY = 5  # seconds between reconnect attempts


class MexcStreamAPI:
    """
    WebSocket client for MEXC perpetual swap market data.

    Subscribes to one or more channels (kline, depth, funding rate) and
    dispatches incoming messages to caller-supplied callbacks. Runs entirely
    in background daemon threads so it doesn't block the Flask process.

    If the connection drops unexpectedly, the client will attempt to reconnect
    up to _MAX_RETRIES times (default 3) with a _RETRY_DELAY second pause
    between each attempt. If all attempts are exhausted, on_disconnect is
    called (if provided) and the client stops.

    Args:
        on_kline:      Called with the data payload when a kline push arrives.
        on_depth:      Called with the data payload when a depth push arrives.
        on_funding:    Called with the data payload when a funding rate push arrives.
        on_disconnect: Called with no arguments when the connection is permanently
                       lost after all reconnect attempts are exhausted.
        ping_interval: Seconds between keep-alive pings (default 20).

    Usage:
        stream = MexcStreamAPI(on_kline=my_kline_handler)
        stream.start(["kline.BTC_USDT.Min15", "depth.ETH_USDT"])
        # ... later ...
        stream.stop()
    """

    def __init__(
        self,
        on_kline: Callable[[dict], None] | None = None,
        on_depth: Callable[[dict], None] | None = None,
        on_funding: Callable[[dict], None] | None = None,
        on_disconnect: Callable[[], None] | None = None,
        ping_interval: int = 20,
    ) -> None:
        self.on_kline = on_kline
        self.on_depth = on_depth
        self.on_funding = on_funding
        self.on_disconnect = on_disconnect
        self.ping_interval = ping_interval
        self._stop_event = threading.Event()
        self._subscriptions: list[str] = []
        self.ws_app: WebSocketApp | None = None

    @property
    def subscriptions(self) -> list[str]:
        """
        The list of channel strings currently registered with this client.

        Returns a copy so callers cannot mutate internal state. Returns an
        empty list before start() is called.

        Example channels: "kline.BTC_USDT.Min15", "depth.ETH_USDT", "funding.BTC_USDT"
        """
        return list(self._subscriptions)

    def _send_ping(self) -> None:
        """
        Background thread: sends a JSON ping every ping_interval seconds.

        Errors are caught and printed rather than raised — a failed ping does
        not kill the thread. If the WebSocket reconnects, the thread picks up
        naturally because it accesses self.ws_app by reference each iteration.
        """
        while not self._stop_event.is_set():
            try:
                if self.ws_app:
                    self.ws_app.send(json.dumps({"method": "ping"}))
            except Exception as e:
                print(f"Ping error: {e}")
            time.sleep(self.ping_interval)

    def _subscribe(self) -> None:
        """
        Send subscription messages for every channel in self._subscriptions.

        Called automatically on WebSocket open and again on each reconnect.
        The 0.1s sleep between messages prevents rate-limit rejections from MEXC.

        Channel format expected:
            "kline.{SYMBOL}.{INTERVAL}"  → e.g. "kline.BTC_USDT.Min15"
            "depth.{SYMBOL}"             → e.g. "depth.ETH_USDT"
            "funding.{SYMBOL}"           → e.g. "funding.BTC_USDT"
        """
        for ch in self._subscriptions:
            parts = ch.split(".")
            method, symbol = parts[0], parts[1]
            param: dict = {"symbol": symbol}
            if method == "kline":
                param["interval"] = parts[2]
            msg = {"method": f"sub.{method}", "param": param}
            self.ws_app.send(json.dumps(msg))
            time.sleep(0.1)

    def _on_message(self, ws: WebSocketApp, message: str) -> None:
        """
        Parse an incoming WebSocket frame and route it to the right callback.

        Pong responses are silently dropped. All other dispatch errors are
        caught per-channel so one bad handler cannot break other subscriptions.
        """
        msg = json.loads(message)
        ch = msg.get("channel", "")
        data = msg.get("data")

        if ch == "pong":
            return

        try:
            if ch.startswith("push.kline") and self.on_kline:
                self.on_kline(data)
            elif ch.startswith("push.depth") and self.on_depth:
                self.on_depth(data)
            elif ch.startswith("push.funding.rate") and self.on_funding:
                self.on_funding(data)
        except Exception as e:
            print(f"Handler error in {ch!r}: {e}")

    def _on_error(self, ws: WebSocketApp, error: Exception) -> None:
        """Log WebSocket-level errors without crashing the client."""
        print(f"WebSocket error: {error}")

    def _on_close(self, ws: WebSocketApp, code: int | None, reason: str | None) -> None:
        """Log connection close events. Reconnect logic lives in the run loop."""
        print(f"WebSocket closed: {code} {reason}")

    def _on_open(self, ws: WebSocketApp) -> None:
        """Send subscriptions immediately after the connection is established."""
        print("WebSocket connection opened")
        self._subscribe()

    def start(self, subscriptions: list[str]) -> None:
        """
        Connect to MEXC and begin streaming the requested channels.

        Launches two daemon threads: one for the WebSocket run loop (which
        includes automatic reconnect) and one for keep-alive pings. Both
        threads are daemons so they die automatically when the main process exits.

        If the connection drops, the run loop waits _RETRY_DELAY seconds then
        tries again, up to _MAX_RETRIES times. stop() can interrupt a reconnect
        delay immediately via the stop event.

        Args:
            subscriptions: List of channel strings to subscribe to.
                           Format: "kline.{SYMBOL}.{INTERVAL}", "depth.{SYMBOL}",
                           or "funding.{SYMBOL}".

        Trader use: Call once after creating the instance. Add as many channels
        as needed — all are handled over a single connection.
        """
        self._subscriptions = list(subscriptions)
        self._stop_event.clear()

        def run() -> None:
            threading.Thread(target=self._send_ping, daemon=True).start()
            retries_left = _MAX_RETRIES

            while not self._stop_event.is_set():
                self.ws_app = WebSocketApp(
                    WS_URL,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                self.ws_app.run_forever()

                if self._stop_event.is_set():
                    break

                # Connection dropped without stop() — attempt reconnect.
                if retries_left == 0:
                    print("WebSocket: all reconnect attempts exhausted, giving up.")
                    if self.on_disconnect:
                        self.on_disconnect()
                    break

                retries_left -= 1
                print(
                    f"WebSocket dropped. Reconnecting in {_RETRY_DELAY}s "
                    f"({retries_left + 1} attempt(s) remaining)..."
                )
                # wait() returns True immediately if stop() is called during the delay.
                if self._stop_event.wait(timeout=_RETRY_DELAY):
                    break

        threading.Thread(target=run, daemon=True).start()

    def stop(self) -> None:
        """
        Gracefully shut down the WebSocket connection and all background threads.

        Sets the stop event (which also interrupts any in-progress reconnect
        delay), then closes the WebSocket. The ping thread and run loop will
        both exit on their next check.

        Trader use: Call when shutting down the server or navigating away from
        the watched-pairs view. Safe to call even if start() was never called.
        """
        self._stop_event.set()
        if self.ws_app:
            self.ws_app.close()


if __name__ == "__main__":
    # Dry-run tests — no network calls made.

    disconnected: list[bool] = []

    stream = MexcStreamAPI(
        on_kline=lambda d: None,
        on_depth=lambda d: None,
        on_funding=lambda d: None,
        on_disconnect=lambda: disconnected.append(True),
        ping_interval=20,
    )

    # Before start(): subscriptions list is empty.
    assert stream.subscriptions == [], f"Expected [], got {stream.subscriptions}"

    # Property returns a copy — mutations must not affect internal state.
    copy = stream.subscriptions
    copy.append("should.not.matter")
    assert stream.subscriptions == [], "subscriptions property must return a copy"

    # Simulate start() writing to _subscriptions without connecting.
    channels = ["kline.BTC_USDT.Min15", "depth.ETH_USDT", "funding.BTC_USDT"]
    stream._subscriptions = list(channels)
    assert stream.subscriptions == channels, "subscriptions did not reflect assignment"

    # Callbacks are stored correctly.
    assert stream.on_kline is not None
    assert stream.on_depth is not None
    assert stream.on_funding is not None
    assert stream.on_disconnect is not None

    # Stop event starts clear, sets and clears correctly.
    assert not stream._stop_event.is_set(), "stop event should start clear"
    stream._stop_event.set()
    assert stream._stop_event.is_set()
    stream._stop_event.clear()
    assert not stream._stop_event.is_set()

    # ws_app is None until a connection is established.
    assert stream.ws_app is None

    print("Channels    :", stream.subscriptions)
    print("Ping interval:", stream.ping_interval)
    print("\nAll dry-run checks passed.")
