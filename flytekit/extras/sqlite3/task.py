import contextlib
import os
import shutil
import sqlite3
import tempfile
import typing
from dataclasses import dataclass

import pandas as pd

from flytekit import FlyteContext, kwtypes
from flytekit.common.tasks.raw_container import _get_container_definition
from flytekit.core.base_sql_task import SQLTask
from flytekit.core.base_task import PythonTask
from flytekit.core.context_manager import FlyteContext, ImageConfig, SerializationSettings
from flytekit.core.python_function_task import PythonInstanceTask
from flytekit.core.resources import Resources, ResourceSpec
from flytekit.core.tracker import TrackedInstance
from flytekit.loggers import logger
from flytekit.models import task as _task_model
from flytekit.models.security import Secret, SecurityContext
from flytekit.types.schema import FlyteSchema


def unarchive_file(local_path: str, to_dir: str):
    """
    Unarchive given archive and returns the unarchived file name. It is expected that only one file is unarchived.
    More than one file or 0 files will result in a ``RuntimeError``
    """
    archive_dir = os.path.join(to_dir, "_arch")
    shutil.unpack_archive(local_path, archive_dir)
    # file gets uncompressed into to_dir/_arch/*.*
    files = os.listdir(archive_dir)
    if not files or len(files) == 0 or len(files) > 1:
        raise RuntimeError(f"Uncompressed archive should contain only one file - found {files}!")
    return os.path.join(archive_dir, files[0])


@dataclass
class SQLite3Config(object):
    """
    Use this configuration to configure if sqlite3 files that should be loaded by the task. The file itself is
    considered as a database and hence is treated like a configuration
    The path to a static sqlite3 compatible database file can be
      - within the container
      - or from a publicly downloadable source

    Args:
        uri: default FlyteFile that will be downloaded on execute
        compressed: Boolean that indicates if the given file is a compressed archive. Supported file types are
                        [zip, tar, gztar, bztar, xztar]
    """

    uri: str
    compressed: bool = False


class QQQ(object):

    image = "ghcr.io/flyteorg/sqlite3:latest"
    command = []


class SQLite3Task(PythonInstanceTask[SQLite3Config], SQLTask[SQLite3Config]):
    """
    Makes it possible to run client side SQLite3 queries that optionally return a FlyteSchema object
    """

    _SQLITE_TASK_TYPE = "sqlite"

    def __init__(
        self,
        name: str,
        query_template: str,
        inputs: typing.Optional[typing.Dict[str, typing.Type]] = None,
        task_config: typing.Optional[SQLite3Config] = None,
        output_schema_type: typing.Optional[typing.Type[FlyteSchema]] = None,
        **kwargs,
    ):
        if task_config is None or task_config.uri is None:
            raise ValueError("SQLite DB uri is required.")
        outputs = kwtypes(results=output_schema_type if output_schema_type else FlyteSchema)
        super().__init__(
            name=name,
            task_config=task_config,
            task_type=self._SQLITE_TASK_TYPE,
            query_template=query_template,
            inputs=inputs,
            outputs=outputs,
            **kwargs,
        )

    image = "ghcr.io/flyteorg/sqlite3:latest"
    command = []
    args = [
        "pyflyte-execute",
        "--inputs",
        "{{.input}}",
        "--output-prefix",
        "{{.outputPrefix}}",
        "--raw-output-data-prefix",
        "{{.rawOutputDataPrefix}}",
        "--resolver",
        "default_task_template_resolver",
        "--",
        "{{.taskTemplatePath}}",
    ]

    def get_container(self, settings: SerializationSettings) -> _task_model.Container:
        env = {**settings.env, **self.environment} if self.environment else settings.env
        return _get_container_definition(
            image=self.image,
            command=self.command,
            args=self.args,
            data_loading_config=None,
            environment=env,
            storage_request=self.resources.requests.storage,
            cpu_request=self.resources.requests.cpu,
            gpu_request=self.resources.requests.gpu,
            memory_request=self.resources.requests.mem,
            storage_limit=self.resources.limits.storage,
            cpu_limit=self.resources.limits.cpu,
            gpu_limit=self.resources.limits.gpu,
            memory_limit=self.resources.limits.mem,
        )

    @property
    def output_columns(self) -> typing.Optional[typing.List[str]]:
        c = self.python_interface.outputs["results"].column_names()
        return c if c else None

    def execute(self, **kwargs) -> typing.Any:
        with tempfile.TemporaryDirectory() as temp_dir:
            ctx = FlyteContext.current_context()
            file_ext = os.path.basename(self.task_config.uri)
            local_path = os.path.join(temp_dir, file_ext)
            ctx.file_access.download(self.task_config.uri, local_path)
            if self.task_config.compressed:
                local_path = unarchive_file(local_path, temp_dir)

            print(f"Connecting to db {local_path}")
            with contextlib.closing(sqlite3.connect(local_path)) as con:
                df = pd.read_sql_query(self.get_query(**kwargs), con)
                return df


# has to work with mock/local execute
