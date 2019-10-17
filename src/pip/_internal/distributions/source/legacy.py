# The following comment should be removed at some point in the future.
# mypy: disallow-untyped-defs=False

import logging

from pip._internal.build_env import BuildEnvironment
from pip._internal.distributions.base import AbstractDistribution
from pip._internal.exceptions import InstallationError
from pip._internal.utils.subprocess import runner_with_spinner_message

logger = logging.getLogger(__name__)


class SourceDistribution(AbstractDistribution):
    """Represents a source distribution.

    The preparation step for these needs metadata for the packages to be
    generated, either using PEP 517 or using the legacy `setup.py egg_info`.

    NOTE from @pradyunsg (14 June 2019)
    I expect SourceDistribution class will need to be split into
    `legacy_source` (setup.py based) and `source` (PEP 517 based) when we start
    bringing logic for preparation out of InstallRequirement into this class.
    """

    def get_pkg_resources_distribution(self):
        return self.req.get_dist()

    def prepare_distribution_metadata(self, finder, build_isolation):
        # Prepare for building. We need to:
        #   1. Load pyproject.toml (if it exists)
        #   2. Set up the build environment

        self.req.load_pyproject_toml()
        should_isolate = self.req.use_pep517 and build_isolation
        if should_isolate:
            self._setup_isolation(finder)

        self.req.prepare_metadata()
        self.req.assert_source_matches_version()

    def _setup_isolation(self, finder):
        def _raise_conflicts(conflicting_with, conflicting_reqs):
            format_string = (
                "Some build dependencies for {requirement} "
                "conflict with {conflicting_with}: {description}."
            )
            error_message = format_string.format(
                requirement=self.req,
                conflicting_with=conflicting_with,
                description=', '.join(
                    '{} is incompatible with {}'.format(installed, wanted)
                    for installed, wanted in sorted(conflicting)
                )
            )
            raise InstallationError(error_message)

        # Isolate in a BuildEnvironment and install the build-time
        # requirements.
        self.req.build_env = BuildEnvironment()
        self.req.build_env.install_requirements(
            finder, self.req.pyproject_requires, 'overlay',
            "Installing build dependencies"
        )
        conflicting, missing = self.req.build_env.check_requirements(
            self.req.requirements_to_check
        )
        if conflicting:
            _raise_conflicts("PEP 517/518 supported requirements",
                             conflicting)
        if missing:
            logger.warning(
                "Missing build requirements in pyproject.toml for %s.",
                self.req,
            )
            logger.warning(
                "The project does not specify a build backend, and "
                "pip cannot fall back to setuptools without %s.",
                " and ".join(map(repr, sorted(missing)))
            )
        # Install any extra build dependencies that the backend requests.
        # This must be done in a second pass, as the pyproject.toml
        # dependencies must be installed before we can call the backend.
        with self.req.build_env:
            runner = runner_with_spinner_message(
                "Getting requirements to build wheel"
            )
            backend = self.req.pep517_backend
            with backend.subprocess_runner(runner):
                reqs = backend.get_requires_for_build_wheel()

        conflicting, missing = self.req.build_env.check_requirements(reqs)
        if conflicting:
            _raise_conflicts("the backend dependencies", conflicting)
        self.req.build_env.install_requirements(
            finder, missing, 'normal',
            "Installing backend dependencies"
        )
