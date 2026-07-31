from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Callable, Mapping
from typing import Any

from .ops_filesystem import (
    OpsMigrationCollision,
    directory_open_flags,
    identity_matches,
    same_identity,
    stat_identity,
)


def hash_open_regular_file(fd: int) -> str:
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode):
        raise OpsMigrationCollision(
            "migration source is not a regular file"
        )
    os.lseek(fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    while chunk := os.read(fd, 1024 * 1024):
        digest.update(chunk)
    after = os.fstat(fd)
    if (
        not same_identity(before, after)
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_ctime_ns != after.st_ctime_ns
    ):
        raise OpsMigrationCollision(
            "migration source changed while being read"
        )
    return digest.hexdigest()


def publish_bound_file_at(
    source_parent_fd: int,
    source_name: str,
    destination_parent_fd: int,
    destination_name: str,
    expected_identity: Mapping[str, Any],
    expected_hash: str,
    *,
    publish_open_file: Callable[[int, int, str], None],
) -> None:
    flags = (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    source_fd = os.open(
        source_name,
        flags,
        dir_fd=source_parent_fd,
    )
    try:
        source_stat = os.fstat(source_fd)
        if (
            not identity_matches(
                expected_identity,
                source_stat,
            )
            or hash_open_regular_file(source_fd) != expected_hash
        ):
            raise OpsMigrationCollision(
                f"migration source changed: {source_name}"
            )
        try:
            publish_open_file(
                source_fd,
                destination_parent_fd,
                destination_name,
            )
        except FileExistsError:
            destination_fd = os.open(
                destination_name,
                flags,
                dir_fd=destination_parent_fd,
            )
            try:
                destination_stat = os.fstat(destination_fd)
                if (
                    not same_identity(
                        source_stat,
                        destination_stat,
                    )
                    or hash_open_regular_file(destination_fd)
                    != expected_hash
                ):
                    raise OpsMigrationCollision(
                        f"destination already exists: {destination_name}"
                    )
            finally:
                os.close(destination_fd)
    finally:
        os.close(source_fd)


def _bound_destination_directory(
    parent_fd: int,
    name: str,
    rel_path: str,
    identities: dict[str, Any],
    persist_manifest: Callable[[], None],
) -> int:
    expected_identity = identities.get(rel_path)
    if expected_identity is None:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError as exc:
            raise OpsMigrationCollision(
                f"destination directory is not migration-owned: {rel_path}"
            ) from exc
        os.fsync(parent_fd)
        destination_fd = os.open(
            name,
            directory_open_flags(),
            dir_fd=parent_fd,
        )
        current = os.fstat(destination_fd)
        named = os.stat(
            name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
        if not same_identity(current, named):
            os.close(destination_fd)
            raise OpsMigrationCollision(
                f"destination directory changed during creation: {rel_path}"
            )
        identities[rel_path] = stat_identity(current)
        persist_manifest()
        return destination_fd
    try:
        destination_fd = os.open(
            name,
            directory_open_flags(),
            dir_fd=parent_fd,
        )
    except FileNotFoundError as exc:
        raise OpsMigrationCollision(
            f"migration-owned destination directory is missing: {rel_path}"
        ) from exc
    current = os.fstat(destination_fd)
    named = os.stat(
        name,
        dir_fd=parent_fd,
        follow_symlinks=False,
    )
    if (
        not identity_matches(expected_identity, current)
        or not same_identity(current, named)
    ):
        os.close(destination_fd)
        raise OpsMigrationCollision(
            f"destination directory ownership changed: {rel_path}"
        )
    return destination_fd


def publish_bound_directory_at(
    source_parent_fd: int,
    source_name: str,
    destination_parent_fd: int,
    destination_name: str,
    entry: Mapping[str, Any],
    publication: dict[str, Any],
    *,
    persist_manifest: Callable[[], None],
    publish_open_file: Callable[[int, int, str], None],
) -> None:
    source_fd = os.open(
        source_name,
        directory_open_flags(),
        dir_fd=source_parent_fd,
    )
    destination_fd: int | None = None
    try:
        if not identity_matches(
            entry["identity"],
            os.fstat(source_fd),
        ):
            raise OpsMigrationCollision(
                f"migration source changed: {source_name}"
            )
        identities = publication.get("destination_directories")
        if not isinstance(identities, dict):
            raise OpsMigrationCollision(
                "stored Ops migration has invalid directory ownership"
            )
        destination_fd = _bound_destination_directory(
            destination_parent_fd,
            destination_name,
            ".",
            identities,
            persist_manifest,
        )
        expected_nodes = {
            str(node["path"]): node
            for node in entry["nodes"]
            if isinstance(node, Mapping)
        }
        expected_files = {
            str(file_entry["path"]): file_entry
            for file_entry in entry["files"]
            if isinstance(file_entry, Mapping)
        }

        def publish_directory(
            current_source_fd: int,
            current_destination_fd: int,
            prefix: str,
        ) -> None:
            source_names = sorted(os.listdir(current_source_fd))
            destination_names = set(
                os.listdir(current_destination_fd)
            )
            unexpected = destination_names - set(source_names)
            if unexpected:
                raise OpsMigrationCollision(
                    "destination contains unplanned content: "
                    f"{sorted(unexpected)[0]}"
                )
            for child_name in source_names:
                rel = (
                    f"{prefix}/{child_name}"
                    if prefix
                    else child_name
                )
                expected_node = expected_nodes.get(rel)
                if expected_node is None:
                    raise OpsMigrationCollision(
                        "migration source contains unplanned content: "
                        f"{rel}"
                    )
                source_stat = os.stat(
                    child_name,
                    dir_fd=current_source_fd,
                    follow_symlinks=False,
                )
                if (
                    not identity_matches(
                        expected_node["identity"],
                        source_stat,
                    )
                    or stat.S_ISLNK(source_stat.st_mode)
                ):
                    raise OpsMigrationCollision(
                        f"migration source changed: {rel}"
                    )
                if expected_node["kind"] == "directory":
                    if not stat.S_ISDIR(source_stat.st_mode):
                        raise OpsMigrationCollision(
                            "migration source changed type: "
                            f"{rel}"
                        )
                    child_source_fd = os.open(
                        child_name,
                        directory_open_flags(),
                        dir_fd=current_source_fd,
                    )
                    try:
                        if not identity_matches(
                            expected_node["identity"],
                            os.fstat(child_source_fd),
                        ):
                            raise OpsMigrationCollision(
                                f"migration source changed: {rel}"
                            )
                        child_destination_fd = (
                            _bound_destination_directory(
                                current_destination_fd,
                                child_name,
                                rel,
                                identities,
                                persist_manifest,
                            )
                        )
                        try:
                            publish_directory(
                                child_source_fd,
                                child_destination_fd,
                                rel,
                            )
                        finally:
                            os.close(child_destination_fd)
                    finally:
                        os.close(child_source_fd)
                    continue
                expected_file = expected_files.get(rel)
                if expected_file is None:
                    raise OpsMigrationCollision(
                        f"migration source file is unplanned: {rel}"
                    )
                publish_bound_file_at(
                    current_source_fd,
                    child_name,
                    current_destination_fd,
                    child_name,
                    expected_file["identity"],
                    str(expected_file["sha256"]),
                    publish_open_file=publish_open_file,
                )

        publish_directory(source_fd, destination_fd, "")
        os.fsync(destination_fd)
        os.fsync(destination_parent_fd)
    finally:
        if destination_fd is not None:
            os.close(destination_fd)
        os.close(source_fd)
