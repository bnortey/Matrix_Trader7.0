#!/usr/bin/env python3
"""Matrix Trader External Learner — scheduled analysis service."""

import os
import sys
import time
import sched
import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path

# Load .env
_env_path = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(_env_path):
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())

DB_PATH = os.environ.get('DB_PATH', '/opt/matrix-trader/data/signals.db')
LOG_PATH = os.path.join(os.path.dirname(__file__), 'logs', 'learner.log')
HEARTBEAT_PATH = os.path.join(os.path.dirname(__file__), 'logs', 'last_heartbeat.txt')
RESEARCH_DIR = Path(os.path.dirname(__file__)) / 'research'
RESEARCH_DIR.mkdir(exist_ok=True)

os.makedirs(os.path.join(os.path.dirname(__file__), 'logs'), exist_ok=True)
os.makedirs(os.path.join(os.path.dirname(__file__), 'models'), exist_ok=True)
os.makedirs(os.path.join(os.path.dirname(__file__), 'suggestions'), exist_ok=True)

handler = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=5*1024*1024, backupCount=2)
handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
logging.basicConfig(level=logging.INFO, handlers=[handler, logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)

from analyzer import run_feature_analysis, run_threshold_analysis, run_regime_analysis
from suggester import run_strategy_proposal_check

INTERVAL_FEATURE    = 30 * 60      # 30 min
INTERVAL_THRESHOLD  = 2  * 60 * 60 # 2 hr
INTERVAL_REGIME     = 6  * 60 * 60 # 6 hr
INTERVAL_PROPOSAL   = 24 * 60 * 60 # 24 hr
INTERVAL_HYPOTHESIS = 6  * 60 * 60 # 6 hr
INTERVAL_REEVAL     = 24 * 60 * 60 # 24 hr
INTERVAL_COACH      = 12 * 60 * 60 # 12 hr


def _heartbeat():
    try:
        with open(HEARTBEAT_PATH, 'w') as f:
            f.write(datetime.now(timezone.utc).isoformat())
    except Exception:
        pass


def job_feature():
    t0 = time.time()
    logger.info('=== job_feature START ===')
    try:
        r = run_feature_analysis(DB_PATH)
        logger.info(f'=== job_feature OK in {time.time()-t0:.1f}s, top={r.get("top_predictors", [])[:3]} ===')
    except Exception as e:
        logger.error(f'=== job_feature FAILED: {e} ===')
    _heartbeat()


def job_threshold():
    t0 = time.time()
    logger.info('=== job_threshold START ===')
    try:
        r = run_threshold_analysis(DB_PATH)
        logger.info(f'=== job_threshold OK in {time.time()-t0:.1f}s, strategies={list(r.get("strategies",{}).keys())} ===')
    except Exception as e:
        logger.error(f'=== job_threshold FAILED: {e} ===')


def job_regime():
    t0 = time.time()
    logger.info('=== job_regime START ===')
    try:
        r = run_regime_analysis(DB_PATH)
        logger.info(f'=== job_regime OK in {time.time()-t0:.1f}s ===')
    except Exception as e:
        logger.error(f'=== job_regime FAILED: {e} ===')


def job_proposal():
    t0 = time.time()
    logger.info('=== job_proposal START ===')
    try:
        r = run_strategy_proposal_check(DB_PATH)
        logger.info(f'=== job_proposal OK in {time.time()-t0:.1f}s, suggestions={len(r.get("suggestions",[]))} ===')
    except Exception as e:
        logger.error(f'=== job_proposal FAILED: {e} ===')


def job_hypothesis():
    try:
        t0 = time.time()
        logger.info('[job_hypothesis] starting')
        from researcher import run_hypothesis_discovery
        run_hypothesis_discovery(DB_PATH, RESEARCH_DIR)
        logger.info(f'[job_hypothesis] done in {time.time()-t0:.1f}s')
    except Exception as e:
        logger.error(f'[job_hypothesis] error: {e}', exc_info=True)


def job_brief_reeval():
    try:
        t0 = time.time()
        logger.info('[job_brief_reeval] starting')
        from researcher import run_brief_reeval
        run_brief_reeval(DB_PATH, RESEARCH_DIR)
        logger.info(f'[job_brief_reeval] done in {time.time()-t0:.1f}s')
    except Exception as e:
        logger.error(f'[job_brief_reeval] error: {e}', exc_info=True)




def job_coach_analysis():
    try:
        t0 = time.time()
        logger.info('[job_coach_analysis] starting')
        from coach_analyst import run_coach_analysis
        r = run_coach_analysis(DB_PATH, RESEARCH_DIR)
        logger.info(f'[job_coach_analysis] done in {time.time()-t0:.1f}s, '
                    f'strategies={r["strategies_analyzed"]} briefs={r["briefs_updated"]}')
    except Exception as e:
        logger.error(f'[job_coach_analysis] error: {e}', exc_info=True)

def run_all_once():
    """Run all six jobs sequentially — used for startup and dry-run."""
    job_feature()
    job_threshold()
    job_regime()
    job_proposal()
    job_hypothesis()
    job_brief_reeval()
    job_coach_analysis()


def schedule_loop():
    scheduler = sched.scheduler(time.time, time.sleep)

    def reschedule_feature(delay=INTERVAL_FEATURE):
        scheduler.enter(delay, 1, _run_feature_and_reschedule)

    def _run_feature_and_reschedule():
        job_feature()
        reschedule_feature()

    def reschedule_threshold(delay=INTERVAL_THRESHOLD):
        scheduler.enter(delay, 2, _run_threshold_and_reschedule)

    def _run_threshold_and_reschedule():
        job_threshold()
        reschedule_threshold()

    def reschedule_regime(delay=INTERVAL_REGIME):
        scheduler.enter(delay, 3, _run_regime_and_reschedule)

    def _run_regime_and_reschedule():
        job_regime()
        reschedule_regime()

    def reschedule_proposal(delay=INTERVAL_PROPOSAL):
        scheduler.enter(delay, 4, _run_proposal_and_reschedule)

    def _run_proposal_and_reschedule():
        job_proposal()
        reschedule_proposal()

    def reschedule_hypothesis(delay=INTERVAL_HYPOTHESIS):
        scheduler.enter(delay, 5, _run_hypothesis_and_reschedule)

    def _run_hypothesis_and_reschedule():
        job_hypothesis()
        reschedule_hypothesis()

    def reschedule_reeval(delay=INTERVAL_REEVAL):
        scheduler.enter(delay, 6, _run_reeval_and_reschedule)

    def _run_reeval_and_reschedule():
        job_brief_reeval()
        reschedule_reeval()

    reschedule_feature()
    reschedule_threshold()
    reschedule_regime()
    reschedule_proposal()
    reschedule_hypothesis()
    reschedule_reeval()


    def reschedule_coach(delay=INTERVAL_COACH):
        scheduler.enter(delay, 7, _run_coach_and_reschedule)

    def _run_coach_and_reschedule():
        job_coach_analysis()
        reschedule_coach()

    reschedule_coach()

    logger.info('Scheduler started. Running...')
    scheduler.run()


if __name__ == '__main__':
    logger.info(f'mt-learner starting, DB_PATH={DB_PATH}')
    if '--dry-run' in sys.argv:
        logger.info('DRY RUN: running all jobs once then exiting')
        run_all_once()
        logger.info('DRY RUN: complete, exiting 0')
        sys.exit(0)
    else:
        logger.info('Running initial jobs on startup...')
        run_all_once()
        logger.info('Starting scheduler loop...')
        schedule_loop()
