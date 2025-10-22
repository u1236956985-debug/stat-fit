# main.py
import time
import signal
from core import bot, SHUTDOWN_EVENT, save_state
import handlers  # регистрирует декораторы при импорте
from handlers import start_background_poll, stop_background_poll

def _handle_signal(sig, frame):
    print(f"got signal {sig}, shutting down...")
    try:
        SHUTDOWN_EVENT.set()
        stop_background_poll()
        bot.stop_polling()
    except Exception:
        pass
    finally:
        save_state()

def run_polling():
    delay = 1
    while not SHUTDOWN_EVENT.is_set():
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=10)
            break  # штатный выход
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"polling error: {e}; retry in {delay}s")
            for _ in range(delay * 10):
                if SHUTDOWN_EVENT.is_set():
                    break
                time.sleep(0.1)
            delay = min(delay * 2, 60)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    start_background_poll()
    try:
        run_polling()
    finally:
        _handle_signal("finalize", None)
