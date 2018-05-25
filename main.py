#!/usr/bin/env python3
"""
The main module for starting the application.
"""

### Logging ###
import logging
logging.basicConfig(filename="output.log",
                    level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")

### System ###
from signal import signal, SIGINT

### Local ###
from ui import TerminalGUI


def main():
    """Entry-point of the application"""
    global app
    app = TerminalGUI()
    app.main()


def signal_handler(sig, frame):
    """Intercept SIGINT to allow for graceful shutdown."""
    # pylint: disable-msg=unused-argument
    logging.info("Received SIGINT, stopping...")
    app.exit_program()


if __name__ == "__main__":
    signal(SIGINT, signal_handler)
    main()
    logging.info("Done")
