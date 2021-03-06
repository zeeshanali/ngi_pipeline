#!/usr/bin/env python

from __future__ import print_function

import importlib

from ngi_pipeline.conductor.classes import NGIProject
from ngi_pipeline.database.classes import CharonSession, CharonError
from ngi_pipeline.log.loggers import minimal_logger
from ngi_pipeline.utils.classes import with_ngi_config
from ngi_pipeline.utils.communication import mail_analysis


LOG = minimal_logger(__name__)

@with_ngi_config
def get_engine_for_bp(project, config=None, config_file_path=None):
    """returns a analysis engine module for the given project.

    :param NGIProject project: The project to get the engine from.
    """
    charon_session = CharonSession()
    best_practice_analysis = charon_session.project_get(project.project_id)["best_practice_analysis"]
    try:
        analysis_engine_module_name = config["analysis"]["best_practice_analysis"][best_practice_analysis]["analysis_engine"]
    except KeyError:
        error_msg = ('No analysis engine for best practice analysis "{}" '
                     'specified in configuration file. '
                     'for project {}'.format(best_practice_analysis, project))
        raise RuntimeError(error_msg)
    try:
        analysis_module = importlib.import_module(analysis_engine_module_name)
    except ImportError as e:
        error_msg = ('project "{}" best practice analysis"{}": couldn\'t import '
                     'module "{}": {}'.format(project, best_practice_analysis,
                                              analysis_engine_module_name, e))
        raise RuntimeError(error_msg)
    return analysis_module

@with_ngi_config
def launch_analysis(projects_to_analyze, restart_failed_jobs=False,
                    restart_finished_jobs=False, restart_running_jobs=False,
                    exec_mode="sbatch", config=None, config_file_path=None, quiet=False, manual=False):
    """Launch the appropriate analysis for each fastq file in the project.

    :param list projects_to_analyze: The list of projects (Project objects) to analyze
    :param dict config: The parsed NGI configuration file; optional/has default.
    :param str config_file_path: The path to the NGI configuration file; optional/has default.
    """
    for project in projects_to_analyze: # Get information from Charon regarding which best practice analyses to run
        engine = get_engine_for_bp(project, config, config_file_path)
        engine.local_process_tracking.update_charon_with_local_jobs_status()
    charon_session = CharonSession()
    for project in projects_to_analyze: # Get information from Charon regarding which best practice analyses to run
        project_status = charon_session.project_get(project.project_id)['status']
        if not project_status == "OPEN":
            error_text = ('Data found on filesystem for project "{}" but Charon '
                          'reports its status is not OPEN ("{}"). Not launching '
                          'analysis for this project.'.format(project, project_status))
            LOG.error(error_text)
            if not config.get('quiet'):
                mail_analysis(project_name=project.name, level="ERROR", info_text=error_text)
            continue
        try:
            analysis_module = get_engine_for_bp(project)
        except (RuntimeError, KeyError, CharonError) as e: # BPA missing from Charon?
            LOG.error('Skipping project "{}" because of error: {}'.format(project, e))
            continue
        for sample in project:
            try:
                charon_reported_status = charon_session.sample_get(project.project_id,
                                                                   sample)['analysis_status']
                # Check Charon to ensure this hasn't already been processed
                if charon_reported_status == "UNDER_ANALYSIS":
                    if not restart_running_jobs:
                        error_text = ('Charon reports seqrun analysis for project "{}" '
                                      '/ sample "{}" does not need processing (already '
                                      '"{}")'.format(project, sample, charon_reported_status))
                        LOG.error(error_text)
                        if not config.get('quiet'):
                            mail_analysis(project_name=project.name, sample_name=sample.name,
                                          engine_name=analysis_module.__name__,
                                          level="ERROR", info_text=error_text)
                        continue
                elif charon_reported_status == "ANALYZED":
                    if not restart_finished_jobs:
                        error_text = ('Charon reports seqrun analysis for project "{}" '
                                      '/ sample "{}" does not need processing (already '
                                      '"{}")'.format(project, sample, charon_reported_status))
                        LOG.error(error_text)
                        if not config.get('quiet') and not config.get('manual'):
                            mail_analysis(project_name=project.name, sample_name=sample.name,
                                          engine_name=analysis_module.__name__,
                                          level="ERROR", info_text=error_text)
                        continue
                elif charon_reported_status == "FAILED":
                    if not restart_failed_jobs:
                        error_text = ('FAILED:  Project "{}" / sample "{}" Charon reports '
                                      'FAILURE, manual investigation needed!'.format(project, sample))
                        LOG.error(error_text)
                        if not config.get('quiet'):
                            mail_analysis(project_name=project.name, sample_name=sample.name,
                                          engine_name=analysis_module.__name__,
                                          level="ERROR", info_text=error_text)
                        continue
            except CharonError as e:
                LOG.error(e)
                continue
            try:
                LOG.info('Attempting to launch sample analysis for '
                         'project "{}" / sample "{}" / engine'
                         '"{}"'.format(project, sample, analysis_module.__name__))
                #actual analysis launch
                analysis_module.analyze(project=project,
                                        sample=sample,
                                        restart_finished_jobs=restart_finished_jobs,
                                        restart_running_jobs=restart_running_jobs,
                                        exec_mode=exec_mode,
                                        config=config)
            except Exception as e:
                error_text = ('Cannot process project "{}" / sample "{}" / '
                              'engine "{}" : {}'.format(project, sample,
                                                        analysis_module.__name__,
                                                        e))
                LOG.error(error_text)
                if not config.get("quiet"):
                    mail_analysis(project_name=project.name, sample_name=sample.name,
                                  engine_name=analysis_module.__name__,
                                  level="ERROR", info_text=e)
                continue
