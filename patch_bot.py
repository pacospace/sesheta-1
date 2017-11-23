#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""This is thoth, a dependency updating bot...
"""

__version__ = '0.1.0'

import os
import json
import logging
import shutil
import fileinput
from pathlib import Path

import semver
from tornado import httpclient
from gemfileparser import GemfileParser

from git import Repo, Actor
from git.exc import GitCommandError
from github import Github

MASTER_REPO_URL = 'git@github.com:goern/manageiq.git'
#MASTER_REPO_URL = 'https://github.com/goern/manageiq.git'
GEMNASIUM_STATUS_URL = 'https://api.gemnasium.com/v1/projects/goern/manageiq/dependencies'
GITHUB_REPO_NAME = 'manageiq'
LOCAL_WORK_COPY = './manageiq-workdir'
SSH_CMD = '/home/goern/Source/manageiq-bots/ssh_command'
os.environ["SSH_CMD"] = SSH_CMD

try:
    from dotenv import load_dotenv, find_dotenv

    load_dotenv(find_dotenv())
except:
    pass

DEBUG_LOG_LEVEL = bool(os.getenv('DEBUG', False))

if DEBUG_LOG_LEVEL:
    logging.basicConfig(level=logging.DEBUG,
                        format='%(asctime)s,%(levelname)s,%(filename)s:%(lineno)d,%(message)s')
else:
    logging.basicConfig(level=logging.INFO)

logger = logging.getLogger(__name__)  # pylint: disable=invalid-name



def major(semverlike):
    _semver = None
    try:
        _semver = semver.parse(semverlike)['major']
    except ValueError as ve:
        logging.error("%s: %s" %(ve, semverlike))
        _semver = semverlike.split('.')[0]

    return _semver


def minor(semverlike):
    _semver = None
    try:
        _semver = semver.parse(semverlike)['minor']
    except ValueError as ve:
        logging.error("%s: %s" % (ve, semverlike))
        _semver = semverlike.split('.')[1]

    return _semver


def get_dependency_status(slug):
    http_client = httpclient.HTTPClient()

    try:
        req = httpclient.HTTPRequest(url=GEMNASIUM_STATUS_URL,
                                     auth_username='X',
                                     auth_password=os.getenv('GEMNASIUM_API_KEY'))
        response = http_client.fetch(req)

        with open('gemnasium-manageiq.json', 'w') as file:
            file.write(response.body.decode("utf-8"))
    except httpclient.HTTPError as e:
        # HTTPError is raised for non-200 responses; the response
        # can be found in e.response.
        logging.error(e)
    except Exception as e:
        # Other errors are possible, such as IOError.
        logging.error(e)
    http_client.close()

    # TODO handle exceptions
    data = json.load(open('gemnasium-manageiq.json'))

    return data


def update_minor_dependency(package):
    """update_minor_dependency is a trivial approach to update the given package to it's current stable release.
    This release will be locked in the Gemfile"""

    logging.info("updating %s to %s" % (package['name'],
                                        package['distributions']['stable']))

    OWD = os.getcwd()
    os.chdir(LOCAL_WORK_COPY)

    parser = GemfileParser('Gemfile', 'manageiq')
    deps = parser.parse()

    # lets loop thru all dependencies and if we found the thing we wanna change
    # open the Gemfile, walk thru all lines and change the thing
    for key in deps:
        if deps[key]:
            for dependency in deps[key]:
                if dependency.name == package['name']:
                    with fileinput.input(files=('Gemfile'), inplace=True, backup='.swp') as Gemfile:
                        for line in Gemfile:
                            if '"' + dependency.name + '"' in line:
                                line = line.replace(dependency.requirement,
                                                    package['distributions']['stable'], 1)

                            print(line.replace('\n','',1)) # no new line!

    os.chdir(OWD)


def cleanup(directory):
    """clean up the mess we made..."""
    logging.info("Cleaning up workdir: %s" % directory)

    try:
        shutil.rmtree(directory)
    except FileNotFoundError as fnfe:
        logging.info("Non Fatal Error: " + str(fnfe))


def pr_in_progress(target_branch):
    """pr_in_progress() will check if there is an open PR from target_branch to master"""
    # TODO
    return not True


if __name__ == '__main__':
    # set some ssh options so that git works flawlessly
    """
    if not os.path.exists(str(Path.home()) + ".ssh/"):
        os.makedirs(str(Path.home()) + ".ssh/")

    with open(str(Path.home()) + ".ssh/config", "w") as ssh_config:
        print("Host github.com\n\tStrictHostKeyChecking no\n", file=ssh_config)
    """

    # and request current status from gemnasium
    deps = get_dependency_status('goern/manageiq')

    # check if gemnasium is up to date...
    if not deps[0]['requirement']:
        logging.debug(deps[0])
        logging.error('Gemnasium is outdated...')
        exit(-1)

    # clone our github repository
    cleanup(LOCAL_WORK_COPY)
    try:
        logging.info("Cloning git repository %s to %s" % (MASTER_REPO_URL, LOCAL_WORK_COPY))
        repository = Repo.clone_from(MASTER_REPO_URL, LOCAL_WORK_COPY)
    except GitCommandError as git_error:
        logging.error(git_error)
        exit(-1)

    # lets have a look at all dependencies
    for dep in deps:
        if dep['color'] == 'yellow': # and at first, just the yellow ones
            logging.debug(dep)
            # if we have no major version shift, lets update the Gemfile
            if major(dep['requirement'].split(' ', 1)[1]) == major(dep['package']['distributions']['stable']):
#            if ((major(dep['locked']) == major(dep['package']['distributions']['stable'])) and
#                    (minor(dep['locked']) < minor(dep['package']['distributions']['stable']))):
                target_branch = 'bots-life/updating-' + dep['package']['name']

                if not pr_in_progress(target_branch):
                    # 1. create a new branch
                    new_branch = repository.create_head(target_branch)
                    new_branch.checkout()

                    # 2. update Gemfile and Gemfile.lock
                    update_minor_dependency(dep['package'])

                    # 3. commit work
 
                    repository.index.add(['Gemfile'])
                    author = Actor('Thoth Dependency Bot',
                                   'goern+sesheta@redhat.com')
                    committer = Actor('Thoth Dependency Bot',
                                      'goern+sesheta@redhat.com')
                    repository.index.commit('Updating {} from {} to {}'.format(dep['package']['name'], 
                                                                                dep['requirement'],
                                                                                dep['package']['distributions']['stable']),
                                            author=author, committer=committer)

                    # 4. push to origin
                    with repository.git.custom_environment(GIT_SSH_COMMAND=SSH_CMD):
                        repository.remotes.origin.push(refspec='{}:{}'.format(
                            target_branch, target_branch))

                    # 5. checkout master
                    repository.refs.master.checkout()
                else:
                    logging.info("There is an open PR for %s, aborting..." %
                        (target_branch))               

            else:
                logging.info("NOT updating %s %s -> %s" % (dep['package']['name'],
                                                  dep['requirement'],
                                                  dep['package']['distributions']['stable']))
