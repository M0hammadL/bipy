"""Utility functionality for logging.
"""
import os
import sys

import logging

from bcbio import utils

LOG_NAME = "bipy"

logger = logging.getLogger(LOG_NAME)

def setup_logging(config):
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        formatter = logging.Formatter('[%(asctime)s] %(message)s')
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        log_dir = config.get("log_dir", None)
        if log_dir:
            logfile = os.path.join(utils.safe_makedir(log_dir),
                                   "{0}.log".format(LOG_NAME))
            handler = logging.FileHandler(logfile)
            handler.setFormatter(formatter)
            logger.addHandler(handler)

import logbook
logger2 = logbook.Logger(LOG_NAME)

def create_log_handler(config):
    log_dir = config.get("log_dir", None)
    email = config.get("email", None)

    if log_dir:
        utils.safe_makedir(log_dir)
        handler = logbook.FileHandler(os.path.join(log_dir, "%s.log" % LOG_NAME))
    else:
        handler = logbook.StreamHandler(sys.stdout)
 
    if email:
        handler = logbook.MailHandler(email, [email], 
                                      format_string=u'''Subject: [BCBB pipeline] {record.extra[run]} \n\n {record.message}''',
                                      level='INFO', bubble = True)
    return handler
