"""Blender 5.2 data creation for decoded Killzone 2 assets."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import bpy
from mathutils import Matrix, Quaternion, Vector

from .import_materials import MaterialBuilder
from .kz2_assets.animation_graph import animation_state_clip_names
from .kz2_assets.core import CoreFile, CoreObject, CoreReference
from .kz2_assets.decoder import CoreDecoder
from .kz2_assets.edge_animation import (
    EdgeAnimation,
    read_skeleton_animation_bone_map_reference,
)
from .kz2_assets.errors import FormatError
from .kz2_assets.geometry import (
    blender_uv_pairs,
    decode_semantic,
    reconstruct_skin_position,
    reconstruct_static_position,
)
from .kz2_assets.level import EntityPlaceHolder, StaticMeshInstance
from .kz2_assets.level_catalog import CachedLevel
from .kz2_assets.model import (
    BoneBindings,
    DestructibilityPartState,
    EntityResource,
    IndexArray,
    LodMesh,
    Matrix4,
    ModelPart,
    ModelResource,
    MultiMesh,
    Primitive,
    RenderEffect,
    Skeleton,
    SkeletonAnimation,
    SkeletonAnimationBoneMap,
    SkinInfo,
    SkinnedMesh,
    SkinVertex,
    StateBasedDestructibility,
    StaticMesh,
    SwitchMesh,
    VertexArray,
    VertexSemantic,
)
from .kz2_assets.psarc import Psarc, PsarcEntry
from .kz2_assets.resolver import PsarcResourceResolver, ResolvedResource

_LOG = logging.getLogger(__name__)


@dataclass(slots=True)
class ImportOptions:
    global_scale: float = 1.0
    flip_uv_v: bool = True
    import_static_meshes: bool = True
    import_skinned_meshes: bool = True
    import_skeletons: bool = True
    import_animations: bool = True
    import_materials: bool = True
    import_level_objects: bool = True
    import_level_entities: bool = True
    include_skybox: bool = True
    convert_map_axes: bool = False
    source_psarc: str = ""
    resource_index_path: str = ""


@dataclass(slots=True)
class ImportResult:
    meshes: int = 0
    level_instances: int = 0
    entity_instances: int = 0
    armatures: int = 0
    actions: int = 0
    materials: int = 0
    images: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _ActionJoint:
    name: str
    rest_local: Matrix
    parent_index: int | None
    animation_default_local: Matrix
    animation_parent_index: int | None


@dataclass(frozen=True, slots=True)
class _MeshLeaf:
    decoder: CoreDecoder
    record: CoreObject
    mesh: StaticMesh
    wrapper_path: tuple[int, ...]
    lod_distance: float | None
    lod_max_distance: float | None
    lod_part_index: int | None
    wrapper_transforms: tuple[Matrix4, ...]
    switch_key: str | None
    switch_default_key: str | None


@dataclass(frozen=True, slots=True)
class _SkinnedMeshLeaf:
    decoder: CoreDecoder
    record: CoreObject
    mesh: SkinnedMesh
    wrapper_path: tuple[int, ...]
    lod_distance: float | None
    lod_max_distance: float | None
    lod_part_index: int | None
    wrapper_transforms: tuple[Matrix4, ...]
    switch_key: str | None
    switch_default_key: str | None


@dataclass(frozen=True, slots=True)
class _MeshTemplate:
    mesh_data: bpy.types.Mesh
    render_effect: CoreReference
    render_effect_decoder: CoreDecoder


@dataclass(frozen=True, slots=True)
class _SkinnedMeshTemplate:
    mesh_data: bpy.types.Mesh
    render_effect: CoreReference
    render_effect_decoder: CoreDecoder
    skin_vertices: tuple[SkinVertex, ...]
    bone_bindings: BoneBindings | None
    skeleton_decoder: CoreDecoder | None
    skeleton_record: CoreObject | None
    skeleton: Skeleton | None


@dataclass(frozen=True, slots=True)
class _EntityArmature:
    object: bpy.types.Object
    decoder: CoreDecoder
    record: CoreObject
    skeleton: Skeleton


def import_core_file(
    context: bpy.types.Context,
    filepath: str,
    options: ImportOptions,
) -> ImportResult:
    started = time.perf_counter()
    _LOG.info("Importing extracted CORE: %s", filepath)
    core = CoreFile.from_path(filepath)
    _LOG.info("Parsed CORE with %d reflected objects", len(core.objects))
    decoder = CoreDecoder(core)
    result = ImportResult()
    collection = _new_import_collection(context, Path(filepath).stem)
    entity_placeholders = (
        [
            (record, decoded)
            for record, decoded in decoder.iter_type("EntityPlaceHolder")
            if isinstance(decoded, EntityPlaceHolder)
        ]
        if options.import_level_objects
        else []
    )
    external_resolver = None
    if options.source_psarc and (
        options.import_static_meshes
        or options.import_skinned_meshes
        or options.import_materials
    ):
        _LOG.info(
            "Indexing external dependencies from PSARC: %s",
            options.source_psarc,
        )
        external_resolver = PsarcResourceResolver(
            options.source_psarc,
            _external_resource_ids(decoder),
            requested_names={
                (
                    placeholder.resource_class_name,
                    placeholder.resource_name,
                )
                for _, placeholder in entity_placeholders
            },
            resource_index_path=options.resource_index_path or None,
        )
        if options.import_materials:
            external_resolver.add_requested_ids(
                _external_render_effect_dependency_ids(
                    decoder,
                    external_resolver,
                )
            )
        _LOG.info(
            "External dependency index ready: %d IDs and %d named entities",
            len(external_resolver.requested_ids),
            len(external_resolver.requested_names),
        )
        if external_resolver.missing_ids:
            result.warnings.append(
                f"{len(external_resolver.missing_ids)} external resource IDs "
                "were not found in the configured PSARC"
            )
        if external_resolver.missing_names:
            result.warnings.append(
                f"{len(external_resolver.missing_names)} entity resource names "
                "were not found in the configured PSARC"
            )
    material_builder = (
        MaterialBuilder(
            decoder,
            result.warnings,
            external_resolver=external_resolver,
            flip_uv_v=options.flip_uv_v,
        )
        if options.import_materials
        else None
    )

    armatures: dict[int, bpy.types.Object] = {}
    skeletons: dict[int, Skeleton] = {}
    if options.import_skeletons or options.import_skinned_meshes or options.import_animations:
        for record, decoded in decoder.iter_type("Skeleton"):
            if not isinstance(decoded, Skeleton):
                continue
            skeletons[record.index] = decoded
            if options.import_skeletons or options.import_skinned_meshes:
                armatures[record.index] = _create_armature(
                    context,
                    collection,
                    record,
                    decoded,
                    options,
                )
                result.armatures += 1

    level_instances = (
        [
            (record, decoded)
            for record, decoded in decoder.iter_type("StaticMeshInstance")
            if isinstance(decoded, StaticMeshInstance)
        ]
        if options.import_level_objects
        else []
    )
    _LOG.info(
        "Import plan: %d level placements, %d entity placements, "
        "%d skeletons",
        len(level_instances),
        len(entity_placeholders),
        len(skeletons),
    )
    if options.import_static_meshes and level_instances:
        result.meshes += _create_level_static_instances(
            collection,
            decoder,
            level_instances,
            options,
            result,
            material_builder,
            external_resolver,
        )
        result.level_instances = len(level_instances)
    elif options.import_static_meshes and not entity_placeholders:
        for record, decoded in decoder.iter_type("StaticMeshResource"):
            if isinstance(decoded, StaticMesh):
                result.meshes += _create_static_mesh(
                    collection,
                    decoder,
                    record,
                    decoded,
                    options,
                    result,
                    material_builder,
                )

    if options.import_static_meshes and entity_placeholders:
        result.meshes += _create_level_entity_instances(
            collection,
            decoder,
            entity_placeholders,
            options,
            result,
            material_builder,
            external_resolver,
        )
        result.entity_instances = len(entity_placeholders)

    if options.import_skinned_meshes:
        for record, decoded in decoder.iter_type("RegularSkinnedMeshResource"):
            if isinstance(decoded, SkinnedMesh):
                result.meshes += _create_skinned_mesh(
                    collection,
                    decoder,
                    record,
                    decoded,
                    armatures,
                    options,
                    result,
                    material_builder,
                )

    if options.import_animations:
        active_armature = (
            context.active_object
            if context.active_object and context.active_object.type == "ARMATURE"
            else None
        )
        for record, decoded in decoder.iter_type("SkeletonAnimationResource"):
            if not isinstance(decoded, SkeletonAnimation):
                continue
            clip = decoded.clip
            if not isinstance(clip, EdgeAnimation):
                continue
            if not clip.decode_supported:
                result.warnings.append(clip.unsupported_reason)
                continue
            transform_channel_count = sum(clip.counts[:3]) + sum(clip.counts[4:7])
            if transform_channel_count == 0:
                result.warnings.append(
                    f"Animation {record.display_id} contains only scalar/user "
                    "channels, which are not mapped to Blender properties"
                )
                continue
            target_index = _find_matching_skeleton(clip, skeletons)
            target = armatures.get(target_index) if target_index is not None else None
            skeleton = skeletons.get(target_index) if target_index is not None else None
            if (
                target is None
                and active_armature is not None
                and int(active_armature.get("kz2_joint_count", -1))
                == clip.joint_count
            ):
                target = active_armature
            if target is None:
                result.warnings.append(
                    f"Animation {record.display_id} has {clip.joint_count} joints, "
                    "but no unique matching imported skeleton was found"
                )
                continue
            action_joints = _action_joints(target, skeleton, options.global_scale)
            _create_action(
                target,
                action_joints,
                record,
                decoded,
                float(target.get("kz2_global_scale", options.global_scale)),
                context.scene.render.fps / context.scene.render.fps_base,
            )
            result.actions += 1
            user_channel_count = clip.counts[3] + clip.counts[7]
            if user_channel_count:
                result.warnings.append(
                    f"Animation {record.display_id}: imported transform tracks "
                    f"and skipped {user_channel_count} scalar/user channels"
                )

    if material_builder is not None:
        result.materials = material_builder.materials_created
        result.images = material_builder.images_created
    _LOG.info(
        "CORE import complete in %.1fs: %d meshes, %d armatures, "
        "%d materials, %d images, %d actions, %d warnings",
        time.perf_counter() - started,
        result.meshes,
        result.armatures,
        result.materials,
        result.images,
        result.actions,
        len(result.warnings),
    )
    return result


def import_psarc_level(
    context: bpy.types.Context,
    archive_path: str,
    level: CachedLevel,
    section_index: int | None,
    options: ImportOptions,
    *,
    progress: Callable[[int, int, str], None] | None = None,
) -> ImportResult:
    started = time.perf_counter()
    if section_index is None:
        selected_sections = level.sections
        import_label = level.display_name
    else:
        if not 0 <= section_index < len(level.sections):
            raise FormatError(
                "cached level section is out of range; refresh the map catalog",
                source=level.root_path,
                expected=f"0..{max(0, len(level.sections) - 1)}",
                observed=section_index,
            )
        selected_sections = (level.sections[section_index],)
        import_label = (
            f"{level.display_name} - "
            f"{level.sections[section_index].display_name}"
        )

    archive = Psarc.open(archive_path)
    entry_sections: dict[
        tuple[int, int, int],
        tuple[PsarcEntry, str, list[str]],
    ] = {}
    for section in selected_sections:
        for path in section.core_paths:
            if not options.include_skybox and "skybox" in path.casefold():
                continue
            try:
                entry = archive.get(path)
            except KeyError as exc:
                raise FormatError(
                    "cached level CORE is not present in the configured PSARC",
                    source=str(archive.path),
                    observed=path,
                ) from exc
            existing = entry_sections.get(entry.physical_key)
            if existing is None:
                entry_sections[entry.physical_key] = (
                    entry,
                    entry.path or path,
                    [section.key],
                )
            elif section.key not in existing[2]:
                existing[2].append(section.key)
    if not entry_sections:
        raise FormatError(
            "the selected level has no importable CORE entries",
            source=level.root_path,
        )

    _LOG.info(
        "Importing %s from %d unique PSARC CORE entries",
        import_label,
        len(entry_sections),
    )
    result = ImportResult()
    root_collection = _new_import_collection(context, import_label)
    root_collection["kz2_level_key"] = level.key
    root_collection["kz2_level_kind"] = level.kind
    root_collection["kz2_level_root"] = level.root_path
    section_collections: dict[str, bpy.types.Collection] = {}
    shared_section_keys = {
        section_key
        for _, _, section_keys in entry_sections.values()
        if len(section_keys) > 1
        for section_key in section_keys
    }
    for section in selected_sections:
        child = bpy.data.collections.new(section.display_name)
        root_collection.children.link(child)
        child["kz2_level_section"] = section.key
        section_collections[section.key] = child
    shared_collection: bpy.types.Collection | None = None

    external_resolver = PsarcResourceResolver(
        archive,
        set(),
        resource_index_path=options.resource_index_path or None,
    )
    static_templates: dict[tuple[int, int, int], _MeshTemplate] = {}
    skinned_templates: dict[
        tuple[int, int, int],
        _SkinnedMeshTemplate,
    ] = {}
    total = len(entry_sections)
    for core_number, (entry, path, section_keys) in enumerate(
        entry_sections.values(),
        1,
    ):
        if progress is not None:
            progress(core_number - 1, total, path)
        _LOG.info(
            "Level import progress: %d/%d CORE entries - %s",
            core_number,
            total,
            path,
        )
        core = CoreFile.from_bytes(
            archive.read_entry(entry),
            source=path,
        )
        decoder = CoreDecoder(core)
        level_instances = [
            (record, decoded)
            for record, decoded in decoder.iter_type("StaticMeshInstance")
            if isinstance(decoded, StaticMeshInstance)
        ]
        entity_placeholders = (
            [
                (record, decoded)
                for record, decoded in decoder.iter_type(
                    "EntityPlaceHolder"
                )
                if isinstance(decoded, EntityPlaceHolder)
            ]
            if options.import_level_entities
            else []
        )
        if not level_instances and not entity_placeholders:
            _LOG.info("Skipping CORE with no render placements: %s", path)
            continue

        external_resolver.add_requested_ids(_external_resource_ids(decoder))
        external_resolver.add_requested_names(
            {
                (
                    placeholder.resource_class_name,
                    placeholder.resource_name,
                )
                for _, placeholder in entity_placeholders
            }
        )
        if options.import_materials:
            external_resolver.add_requested_ids(
                _external_render_effect_dependency_ids(
                    decoder,
                    external_resolver,
                )
            )
        material_builder = (
            MaterialBuilder(
                decoder,
                result.warnings,
                external_resolver=external_resolver,
                flip_uv_v=options.flip_uv_v,
            )
            if options.import_materials
            else None
        )
        if len(section_keys) == 1:
            collection = section_collections[section_keys[0]]
        else:
            if shared_collection is None:
                shared_collection = bpy.data.collections.new("Shared")
                root_collection.children.link(shared_collection)
                shared_collection["kz2_level_sections"] = [
                    section.display_name
                    for section in selected_sections
                    if section.key in shared_section_keys
                ]
            collection = shared_collection

        if options.import_static_meshes and level_instances:
            result.meshes += _create_level_static_instances(
                collection,
                decoder,
                level_instances,
                options,
                result,
                material_builder,
                external_resolver,
                static_templates=static_templates,
                skinned_templates=skinned_templates,
            )
            result.level_instances += len(level_instances)
        if options.import_static_meshes and entity_placeholders:
            result.meshes += _create_level_entity_instances(
                collection,
                decoder,
                entity_placeholders,
                options,
                result,
                material_builder,
                external_resolver,
                static_templates=static_templates,
                skinned_templates=skinned_templates,
                context=context,
            )
            result.entity_instances += len(entity_placeholders)
        if material_builder is not None:
            result.materials += material_builder.materials_created
            result.images += material_builder.images_created

    if progress is not None:
        progress(total, total, "")
    if external_resolver.missing_ids:
        result.warnings.append(
            f"{len(external_resolver.missing_ids)} external resource IDs "
            "were not found in the configured PSARC"
        )
    if external_resolver.missing_names:
        result.warnings.append(
            f"{len(external_resolver.missing_names)} entity resource names "
            "were not found in the configured PSARC"
        )
    if not result.level_instances and not result.entity_instances:
        result.warnings.append(
            "The selected level data contained no supported render placements"
        )
    _LOG.info(
        "Level import complete in %.1fs: %d CORE entries, %d meshes, "
        "%d static placements, %d entity placements, %d materials, "
        "%d images, %d warnings",
        time.perf_counter() - started,
        total,
        result.meshes,
        result.level_instances,
        result.entity_instances,
        result.materials,
        result.images,
        len(result.warnings),
    )
    return result


def import_psarc_entity(
    context: bpy.types.Context,
    archive_path: str,
    entity_path: str,
    object_index: int,
    options: ImportOptions,
) -> ImportResult:
    started = time.perf_counter()
    _LOG.info(
        "Importing cached entity object %d from %s",
        object_index,
        entity_path,
    )
    archive = Psarc.open(archive_path)
    try:
        entry = archive.get(entity_path)
    except KeyError as exc:
        raise FormatError(
            "cached entity CORE is not present in the configured PSARC",
            source=str(archive.path),
            observed=entity_path,
        ) from exc
    core = CoreFile.from_bytes(
        archive.read_entry(entry),
        source=entry.path or entity_path,
    )
    if not 0 <= object_index < len(core.objects):
        raise FormatError(
            "cached entity object index is out of range; rescan the PSARC",
            source=core.source,
            expected=f"0..{max(0, len(core.objects) - 1)}",
            observed=object_index,
        )
    record = core.objects[object_index]
    if record.type_name != "EntityResource":
        raise FormatError(
            "cached entity object type changed; rescan the PSARC",
            source=core.source,
            expected="EntityResource",
            observed=record.type_name,
        )
    decoder = CoreDecoder(core)
    entity_value = decoder.decode(record)
    if not isinstance(entity_value, EntityResource):
        raise FormatError(
            "cached entity object could not be decoded",
            source=core.source,
            observed=record.type_name,
        )

    result = ImportResult(entity_instances=1)
    collection = _new_import_collection(
        context,
        entity_value.prefix.name or f"Entity_{object_index:05d}",
    )
    external_resolver = PsarcResourceResolver(
        archive,
        set(),
        resource_index_path=options.resource_index_path or None,
    )
    material_builder = (
        MaterialBuilder(
            decoder,
            result.warnings,
            external_resolver=external_resolver,
            flip_uv_v=options.flip_uv_v,
        )
        if options.import_materials
        else None
    )

    placement_name = (
        entity_value.prefix.name or f"EntityResource_{object_index:05d}"
    )
    placement = bpy.data.objects.new(placement_name, None)
    placement.empty_display_type = "CUBE"
    placement.empty_display_size = max(0.25, options.global_scale)
    collection.objects.link(placement)
    _set_source_properties(placement, record)
    placement["kz2_cached_entity"] = True
    placement["kz2_entity_resource_class"] = record.type_name
    placement["kz2_entity_resource_name"] = entity_value.prefix.name
    placement["kz2_entity_resource_object_id"] = record.display_id
    placement["kz2_entity_resource_source"] = core.source

    entity_armatures: dict[tuple[int, int], _EntityArmature] = {}
    result.meshes = _create_entity_render_objects(
        collection,
        placement,
        ResolvedResource(decoder, record, entity_value),
        f"{record.display_id} ({placement_name})",
        record,
        options,
        result,
        material_builder,
        external_resolver,
        {},
        {},
        None,
        context=context,
        armatures=entity_armatures,
    )
    if options.import_animations:
        result.actions = _create_cached_entity_actions(
            context,
            placement,
            decoder,
            entity_armatures,
            options,
            result,
            external_resolver,
        )
        if result.actions and len(entity_armatures) == 1:
            _activate_cached_entity_armature(
                context,
                next(iter(entity_armatures.values())).object,
            )
    if external_resolver.missing_ids:
        result.warnings.append(
            f"{len(external_resolver.missing_ids)} external resource IDs "
            "were not found in the configured PSARC"
        )
    if material_builder is not None:
        result.materials = material_builder.materials_created
        result.images = material_builder.images_created
    _LOG.info(
        "Entity import complete in %.1fs: %d meshes, %d armatures, "
        "%d materials, %d images, %d actions, %d warnings",
        time.perf_counter() - started,
        result.meshes,
        result.armatures,
        result.materials,
        result.images,
        result.actions,
        len(result.warnings),
    )
    return result


def _activate_cached_entity_armature(
    context: bpy.types.Context,
    armature_object: bpy.types.Object,
) -> None:
    for selected in tuple(context.selected_objects):
        selected.select_set(False)
    armature_object.select_set(True)
    context.view_layer.objects.active = armature_object
    action = (
        None
        if armature_object.animation_data is None
        else armature_object.animation_data.action
    )
    if action is not None and action.use_frame_range:
        context.scene.frame_set(round(action.frame_start))


def _external_resource_ids(decoder: CoreDecoder) -> set[str]:
    external_ids: set[str] = set()

    def add(reference: CoreReference) -> None:
        if reference.external_id is not None:
            external_ids.add(reference.external_id)

    for _, decoded in decoder.iter_type(
        "PrimitiveResource",
        "RenderingPrimitiveResource",
    ):
        if not isinstance(decoded, Primitive):
            continue
        for reference in (
            decoded.vertex_array,
            decoded.index_array,
            decoded.spatial_tree,
            decoded.render_effect,
        ):
            add(reference)
    for _, decoded in decoder.iter_type("RenderEffectResource"):
        if not isinstance(decoded, RenderEffect):
            continue
        for technique in decoded.techniques:
            add(technique.shader)
            for binding in technique.texture_bindings:
                add(binding.texture)
    for _, decoded in decoder.iter_type("EntityResource"):
        if not isinstance(decoded, EntityResource):
            continue
        add(decoded.animator)
        add(decoded.mover)
        add(decoded.model)
        add(decoded.destructibility)
    for _, decoded in decoder.iter_type(
        "StaticModelResource",
        "SkinnedModelResource",
    ):
        if isinstance(decoded, ModelResource):
            add(decoded.mesh)
    for _, decoded in decoder.iter_type("ModelPartResource"):
        if isinstance(decoded, ModelPart):
            add(decoded.mesh)
    for _, decoded in decoder.iter_type("DestructibilityPartStateResource"):
        if isinstance(decoded, DestructibilityPartState):
            add(decoded.model_part)
    for _, decoded in decoder.iter_type("StateBasedDestructibilityResource"):
        if not isinstance(decoded, StateBasedDestructibility):
            continue
        for reference in decoded.part_states:
            add(reference)
    for obj in decoder.core.objects:
        instance = obj.direct_value
        if isinstance(instance, EntityPlaceHolder):
            for reference in instance.linked_objects:
                add(reference)
            continue
        if not isinstance(instance, StaticMeshInstance):
            continue
        add(instance.static_model)
        for mesh_override in instance.shader_overrides.mesh_overrides:
            add(mesh_override.mesh)
            for effect_override in mesh_override.render_effect_overrides:
                add(effect_override.render_effect)
                for texture_override in effect_override.texture_overrides:
                    add(texture_override.texture)
                for variable_override in effect_override.variable_overrides:
                    add(variable_override.animator)
    return external_ids


def _external_render_effect_dependency_ids(
    decoder: CoreDecoder,
    resolver: PsarcResourceResolver,
) -> set[str]:
    external_ids: set[str] = set()
    effect_ids = {
        decoded.render_effect.external_id
        for _, decoded in decoder.iter_type(
            "PrimitiveResource",
            "RenderingPrimitiveResource",
        )
        if isinstance(decoded, Primitive)
        and decoded.render_effect.external_id is not None
    }
    for effect_id in effect_ids:
        resolved = resolver.resolve(
            effect_id,
            expected_type="RenderEffectResource",
        )
        if resolved is None or not isinstance(resolved.value, RenderEffect):
            continue
        for technique in resolved.value.techniques:
            if technique.shader.external_id is not None:
                external_ids.add(technique.shader.external_id)
            for binding in technique.texture_bindings:
                if binding.texture.external_id is not None:
                    external_ids.add(binding.texture.external_id)
    return external_ids


def _new_import_collection(
    context: bpy.types.Context,
    name: str,
) -> bpy.types.Collection:
    collection = bpy.data.collections.new(f"KZ2_{name}")
    context.scene.collection.children.link(collection)
    collection["kz2_profile"] = "BCUS98116 01.01 / RTTIBin 1.58"
    return collection


def _create_level_static_instances(
    collection: bpy.types.Collection,
    decoder: CoreDecoder,
    instances: list[tuple[CoreObject, StaticMeshInstance]],
    options: ImportOptions,
    result: ImportResult,
    material_builder: MaterialBuilder | None,
    external_resolver: PsarcResourceResolver | None,
    *,
    static_templates: dict[tuple[int, int, int], _MeshTemplate] | None = None,
    skinned_templates: (
        dict[tuple[int, int, int], _SkinnedMeshTemplate] | None
    ) = None,
) -> int:
    placement_objects: dict[int, bpy.types.Object] = {}
    for record, instance in instances:
        name = instance.name or f"StaticMeshInstance_{record.index:05d}"
        placement = bpy.data.objects.new(name, None)
        placement.empty_display_type = "PLAIN_AXES"
        placement.empty_display_size = max(0.25, options.global_scale)
        collection.objects.link(placement)
        _set_source_properties(placement, record)
        placement["kz2_flags"] = instance.flags
        placement["kz2_drawable_fields"] = (
            instance.geometry.drawable_state.hex()
        )
        placement["kz2_tail_fields"] = instance.tail.hex()
        placement_objects[record.index] = placement

    for record, instance in instances:
        placement = placement_objects[record.index]
        parent_index = instance.geometry.world_node.parent.local_index
        if parent_index is not None:
            parent = placement_objects.get(parent_index)
            if parent is None:
                result.warnings.append(
                    f"StaticMeshInstance {record.display_id} has unresolved "
                    f"parent object {parent_index}"
                )
            else:
                placement.parent = parent
        placement.matrix_local = _level_matrix(
            instance.geometry.world_node.orientation,
            options,
        )

    coordinate_basis = _map_coordinate_basis() if options.convert_map_axes else None
    if static_templates is None:
        static_templates = {}
    if skinned_templates is None:
        skinned_templates = {}
    created = 0
    for record, instance in instances:
        placement = placement_objects[record.index]
        leaves = _resolve_mesh_leaves(
            decoder,
            instance.static_model,
            result,
            external_resolver=external_resolver,
        )
        if not leaves:
            result.warnings.append(
                f"StaticMeshInstance {record.display_id} has no supported "
                "local mesh leaf"
            )
            continue
        for leaf in leaves:
            if isinstance(leaf, _SkinnedMeshLeaf):
                leaf_templates = _skinned_mesh_templates(
                    leaf.decoder,
                    leaf.record,
                    leaf.mesh,
                    options,
                    result,
                    material_builder,
                    external_resolver,
                    skinned_templates,
                    coordinate_basis,
                )
                resource_type = "SkinnedMeshResource"
            else:
                leaf_templates = _static_mesh_templates(
                    leaf,
                    options,
                    result,
                    material_builder,
                    external_resolver,
                    static_templates,
                    coordinate_basis,
                )
                resource_type = "StaticMeshResource"
            for primitive_index, template in enumerate(leaf_templates):
                object_name = (
                    leaf.mesh.prefix.name
                    or f"{resource_type}_{leaf.record.index:05d}"
                )
                mesh_object = bpy.data.objects.new(
                    f"{object_name}_P{primitive_index:02d}",
                    template.mesh_data,
                )
                collection.objects.link(mesh_object)
                mesh_object.parent = placement
                mesh_object.matrix_local = _wrapper_matrix(
                    leaf.wrapper_transforms,
                    options,
                )
                _set_source_properties(mesh_object, record)
                mesh_object["kz2_mesh_object_index"] = leaf.record.index
                mesh_object["kz2_wrapper_path"] = list(leaf.wrapper_path)
                if isinstance(leaf, _SkinnedMeshLeaf):
                    mesh_object["kz2_skinned_rest_pose"] = True
                if leaf.lod_distance is not None:
                    mesh_object["kz2_lod_distance"] = leaf.lod_distance
                    mesh_object["kz2_lod_max_distance"] = (
                        leaf.lod_max_distance
                    )
                    if leaf.lod_part_index not in (None, 0):
                        mesh_object.hide_viewport = True
                        mesh_object.hide_render = True
                if leaf.switch_key is not None:
                    mesh_object["kz2_switch_key"] = leaf.switch_key
                    mesh_object["kz2_switch_default_key"] = (
                        leaf.switch_default_key or ""
                    )
                    if leaf.switch_key != leaf.switch_default_key:
                        mesh_object.hide_viewport = True
                        mesh_object.hide_render = True
                _apply_instance_material_overrides(
                    mesh_object,
                    template.render_effect,
                    template.render_effect_decoder,
                    instance,
                    leaf,
                    record.index,
                    material_builder,
                )
                created += 1
    return created


def _create_level_entity_instances(
    collection: bpy.types.Collection,
    decoder: CoreDecoder,
    placeholders: list[tuple[CoreObject, EntityPlaceHolder]],
    options: ImportOptions,
    result: ImportResult,
    material_builder: MaterialBuilder | None,
    external_resolver: PsarcResourceResolver | None,
    *,
    static_templates: dict[tuple[int, int, int], _MeshTemplate] | None = None,
    skinned_templates: (
        dict[tuple[int, int, int], _SkinnedMeshTemplate] | None
    ) = None,
    context: bpy.types.Context | None = None,
) -> int:
    coordinate_basis = _map_coordinate_basis() if options.convert_map_axes else None
    if static_templates is None:
        static_templates = {}
    if skinned_templates is None:
        skinned_templates = {}
    created = 0

    for record, placeholder in placeholders:
        name = placeholder.name or f"EntityPlaceHolder_{record.index:05d}"
        placement = bpy.data.objects.new(name, None)
        placement.empty_display_type = "CUBE"
        placement.empty_display_size = max(0.25, options.global_scale)
        collection.objects.link(placement)
        placement.matrix_local = _level_matrix(placeholder.orientation, options)
        _set_source_properties(placement, record)
        placement["kz2_entity_resource_class"] = (
            placeholder.resource_class_name
        )
        placement["kz2_entity_resource_name"] = placeholder.resource_name
        placement["kz2_entity_name_is_identifier"] = (
            placeholder.name_is_identifier
        )
        placement["kz2_entity_attributes"] = list(placeholder.attributes)
        placement["kz2_entity_linked_objects"] = [
            _reference_label(decoder, reference)
            for reference in placeholder.linked_objects
        ]
        placement["kz2_destructibility_state"] = (
            f"0x{placeholder.destructibility_state:08x}"
        )

        entity = _resolve_named_entity(
            decoder,
            placeholder,
            external_resolver,
            result,
        )
        if entity is None or not isinstance(entity.value, EntityResource):
            continue
        placement["kz2_entity_resource_object_id"] = (
            entity.record.display_id
        )
        placement["kz2_entity_resource_source"] = entity.decoder.core.source

        created += _create_entity_render_objects(
            collection,
            placement,
            entity,
            f"{record.display_id} ({placeholder.resource_name})",
            record,
            options,
            result,
            material_builder,
            external_resolver,
            static_templates,
            skinned_templates,
            coordinate_basis,
            context=context,
        )
    return created


def _create_entity_render_objects(
    collection: bpy.types.Collection,
    placement: bpy.types.Object,
    entity: ResolvedResource,
    entity_label: str,
    source_record: CoreObject,
    options: ImportOptions,
    result: ImportResult,
    material_builder: MaterialBuilder | None,
    external_resolver: PsarcResourceResolver | None,
    static_templates: dict[tuple[int, int, int], _MeshTemplate],
    skinned_templates: dict[
        tuple[int, int, int],
        _SkinnedMeshTemplate,
    ],
    coordinate_basis: Matrix | None,
    *,
    context: bpy.types.Context | None = None,
    armatures: dict[tuple[int, int], _EntityArmature] | None = None,
) -> int:
    render_references = _entity_render_references(
        entity,
        external_resolver,
        result,
    )
    if not render_references:
        result.warnings.append(
            f"Entity {entity_label} has no supported render-model reference"
        )
        return 0

    created = 0
    bound_armatures = {} if armatures is None else armatures
    for resource_decoder, mesh_reference in render_references:
        resolved_mesh = _resolve_reference(
            resource_decoder,
            mesh_reference,
            external_resolver,
        )
        if resolved_mesh is None:
            result.warnings.append(
                f"Entity {entity_label} has unresolved mesh "
                f"{_reference_label(resource_decoder, mesh_reference)}"
            )
            continue
        if isinstance(resolved_mesh.value, SkinnedMesh):
            templates = _skinned_mesh_templates(
                resolved_mesh.decoder,
                resolved_mesh.record,
                resolved_mesh.value,
                options,
                result,
                material_builder,
                external_resolver,
                skinned_templates,
                coordinate_basis,
            )
            for primitive_index, template in enumerate(templates):
                object_name = (
                    resolved_mesh.value.prefix.name
                    or f"SkinnedMeshResource_{resolved_mesh.record.index:05d}"
                )
                mesh_object = bpy.data.objects.new(
                    f"{object_name}_P{primitive_index:02d}",
                    template.mesh_data,
                )
                collection.objects.link(mesh_object)
                _bind_skinned_template(
                    context,
                    collection,
                    placement,
                    mesh_object,
                    template,
                    options,
                    result,
                    bound_armatures,
                )
                mesh_object.matrix_local = Matrix.Identity(4)
                _set_source_properties(mesh_object, source_record)
                mesh_object["kz2_mesh_object_index"] = (
                    resolved_mesh.record.index
                )
                mesh_object["kz2_mesh_source"] = (
                    resolved_mesh.decoder.core.source
                )
                mesh_object["kz2_skinned_rest_pose"] = True
                created += 1
            continue

        leaves = _resolve_mesh_leaves(
            resource_decoder,
            mesh_reference,
            result,
            external_resolver=external_resolver,
        )
        for leaf in leaves:
            if isinstance(leaf, _SkinnedMeshLeaf):
                templates = _skinned_mesh_templates(
                    leaf.decoder,
                    leaf.record,
                    leaf.mesh,
                    options,
                    result,
                    material_builder,
                    external_resolver,
                    skinned_templates,
                    coordinate_basis,
                )
                resource_type = "SkinnedMeshResource"
            else:
                templates = _static_mesh_templates(
                    leaf,
                    options,
                    result,
                    material_builder,
                    external_resolver,
                    static_templates,
                    coordinate_basis,
                )
                resource_type = "StaticMeshResource"
            for primitive_index, template in enumerate(templates):
                object_name = (
                    leaf.mesh.prefix.name
                    or f"{resource_type}_{leaf.record.index:05d}"
                )
                mesh_object = bpy.data.objects.new(
                    f"{object_name}_P{primitive_index:02d}",
                    template.mesh_data,
                )
                collection.objects.link(mesh_object)
                if isinstance(template, _SkinnedMeshTemplate):
                    _bind_skinned_template(
                        context,
                        collection,
                        placement,
                        mesh_object,
                        template,
                        options,
                        result,
                        bound_armatures,
                    )
                else:
                    mesh_object.parent = placement
                mesh_object.matrix_local = _wrapper_matrix(
                    leaf.wrapper_transforms,
                    options,
                )
                _set_source_properties(mesh_object, source_record)
                mesh_object["kz2_mesh_object_index"] = leaf.record.index
                mesh_object["kz2_mesh_source"] = leaf.decoder.core.source
                mesh_object["kz2_wrapper_path"] = list(leaf.wrapper_path)
                if isinstance(leaf, _SkinnedMeshLeaf):
                    mesh_object["kz2_skinned_rest_pose"] = True
                if leaf.lod_distance is not None:
                    mesh_object["kz2_lod_distance"] = leaf.lod_distance
                    mesh_object["kz2_lod_max_distance"] = (
                        leaf.lod_max_distance
                    )
                    if leaf.lod_part_index not in (None, 0):
                        mesh_object.hide_viewport = True
                        mesh_object.hide_render = True
                if leaf.switch_key is not None:
                    mesh_object["kz2_switch_key"] = leaf.switch_key
                    mesh_object["kz2_switch_default_key"] = (
                        leaf.switch_default_key or ""
                    )
                    if leaf.switch_key != leaf.switch_default_key:
                        mesh_object.hide_viewport = True
                        mesh_object.hide_render = True
                created += 1
    return created


def _resolve_named_entity(
    decoder: CoreDecoder,
    placeholder: EntityPlaceHolder,
    external_resolver: PsarcResourceResolver | None,
    result: ImportResult,
) -> ResolvedResource | None:
    matches = [
        ResolvedResource(decoder, record, decoded)
        for record, decoded in decoder.iter_type(
            placeholder.resource_class_name
        )
        if isinstance(decoded, EntityResource)
        and decoded.prefix.name == placeholder.resource_name
    ]
    identities = {match.record.display_id for match in matches}
    if len(identities) == 1:
        return matches[0]
    if len(identities) > 1:
        result.warnings.append(
            f"Entity resource {placeholder.resource_class_name}:"
            f"{placeholder.resource_name} is ambiguous in the current CORE"
        )
        return None
    if external_resolver is None:
        result.warnings.append(
            f"Entity resource {placeholder.resource_class_name}:"
            f"{placeholder.resource_name} is external, but no source PSARC "
            "is configured"
        )
        return None
    try:
        resolved = external_resolver.resolve_named(
            placeholder.resource_class_name,
            placeholder.resource_name,
            source_path=decoder.core.source,
        )
    except FormatError as exc:
        result.warnings.append(str(exc))
        return None
    if resolved is None:
        result.warnings.append(
            f"Entity resource {placeholder.resource_class_name}:"
            f"{placeholder.resource_name} was not found in the configured PSARC"
        )
    return resolved


def _entity_render_references(
    entity: ResolvedResource,
    external_resolver: PsarcResourceResolver | None,
    result: ImportResult,
) -> tuple[tuple[CoreDecoder, CoreReference], ...]:
    value = entity.value
    if not isinstance(value, EntityResource):
        return ()
    references: list[tuple[CoreDecoder, CoreReference]] = []
    model = _resolve_reference(
        entity.decoder,
        value.model,
        external_resolver,
    )
    if model is not None:
        if isinstance(model.value, ModelResource):
            if not model.value.mesh.is_null:
                references.append((model.decoder, model.value.mesh))
        else:
            result.warnings.append(
                f"EntityResource {entity.record.display_id} model has "
                f"unsupported type {model.record.type_name}"
            )

    destructibility = _resolve_reference(
        entity.decoder,
        value.destructibility,
        external_resolver,
    )
    if destructibility is None:
        return tuple(references)
    if not isinstance(destructibility.value, StateBasedDestructibility):
        result.warnings.append(
            f"EntityResource {entity.record.display_id} destructibility has "
            f"unsupported type {destructibility.record.type_name}"
        )
        return tuple(references)
    if not destructibility.value.part_states_complete:
        result.warnings.append(
            f"EntityResource {entity.record.display_id} uses an unsupported "
            "destructibility layout; damage-state meshes were skipped"
        )
        return tuple(references)
    for state_reference in destructibility.value.part_states:
        state = _resolve_reference(
            destructibility.decoder,
            state_reference,
            external_resolver,
        )
        if state is None or not isinstance(
            state.value,
            DestructibilityPartState,
        ):
            continue
        part = _resolve_reference(
            state.decoder,
            state.value.model_part,
            external_resolver,
        )
        if part is None or not isinstance(part.value, ModelPart):
            continue
        if not part.value.mesh.is_null:
            references.append((part.decoder, part.value.mesh))
    return tuple(references)


def _resolve_reference(
    decoder: CoreDecoder,
    reference: CoreReference,
    external_resolver: PsarcResourceResolver | None,
) -> ResolvedResource | None:
    if reference.local_index is not None:
        record = decoder.core.objects[reference.local_index]
        return ResolvedResource(decoder, record, decoder.decode(record))
    if reference.external_id is None or external_resolver is None:
        return None
    if reference.external_id.casefold() not in external_resolver.requested_ids:
        external_resolver.add_requested_ids({reference.external_id})
    return external_resolver.resolve(reference.external_id)


def _reference_label(
    decoder: CoreDecoder,
    reference: CoreReference,
) -> str:
    if reference.local_index is not None:
        record = decoder.core.objects[reference.local_index]
        return f"local:{reference.local_index}:{record.type_name}"
    if reference.external_id is not None:
        return f"external:{reference.external_id}"
    return "null"


def _resolve_mesh_leaves(
    decoder: CoreDecoder,
    reference: CoreReference,
    result: ImportResult,
    *,
    external_resolver: PsarcResourceResolver | None,
    path: tuple[int, ...] = (),
    lod_distance: float | None = None,
    lod_max_distance: float | None = None,
    lod_part_index: int | None = None,
    wrapper_transforms: tuple[Matrix4, ...] = (),
    switch_key: str | None = None,
    switch_default_key: str | None = None,
    visiting: frozenset[tuple[int, int]] = frozenset(),
) -> tuple[_MeshLeaf | _SkinnedMeshLeaf, ...]:
    if reference.local_index is None:
        if reference.external_id is None:
            return ()
        if external_resolver is None:
            result.warnings.append(
                f"Mesh resource {reference.external_id!r} is external, but "
                "no source PSARC is configured"
            )
            return ()
        if reference.external_id.casefold() not in external_resolver.requested_ids:
            external_resolver.add_requested_ids({reference.external_id})
        resolved = external_resolver.resolve(reference.external_id)
        if resolved is None:
            result.warnings.append(
                f"Mesh resource {reference.external_id!r} was not found in "
                "the configured PSARC"
            )
            return ()
        return _resolve_mesh_leaves(
            resolved.decoder,
            CoreReference(local_index=resolved.record.index),
            result,
            external_resolver=external_resolver,
            path=path,
            lod_distance=lod_distance,
            lod_max_distance=lod_max_distance,
            lod_part_index=lod_part_index,
            wrapper_transforms=wrapper_transforms,
            switch_key=switch_key,
            switch_default_key=switch_default_key,
            visiting=visiting,
        )
    index = reference.local_index
    visit_key = (id(decoder.core), index)
    if visit_key in visiting:
        result.warnings.append(f"Mesh wrapper cycle detected at object {index}")
        return ()
    record = decoder.core.objects[index]
    decoded = decoder.decode(record)
    if isinstance(decoded, StaticMesh):
        return (
            _MeshLeaf(
                decoder=decoder,
                record=record,
                mesh=decoded,
                wrapper_path=path,
                lod_distance=lod_distance,
                lod_max_distance=lod_max_distance,
                lod_part_index=lod_part_index,
                wrapper_transforms=wrapper_transforms,
                switch_key=switch_key,
                switch_default_key=switch_default_key,
            ),
        )
    if isinstance(decoded, SkinnedMesh):
        return (
            _SkinnedMeshLeaf(
                decoder=decoder,
                record=record,
                mesh=decoded,
                wrapper_path=path,
                lod_distance=lod_distance,
                lod_max_distance=lod_max_distance,
                lod_part_index=lod_part_index,
                wrapper_transforms=wrapper_transforms,
                switch_key=switch_key,
                switch_default_key=switch_default_key,
            ),
        )
    if isinstance(decoded, LodMesh):
        leaves: list[_MeshLeaf | _SkinnedMeshLeaf] = []
        nested_visiting = visiting | {visit_key}
        for part_index, lod_part in enumerate(decoded.parts):
            leaves.extend(
                _resolve_mesh_leaves(
                    decoder,
                    lod_part.mesh,
                    result,
                    external_resolver=external_resolver,
                    path=(*path, part_index),
                    lod_distance=lod_part.distance,
                    lod_max_distance=decoded.max_distance,
                    lod_part_index=part_index,
                    wrapper_transforms=wrapper_transforms,
                    switch_key=switch_key,
                    switch_default_key=switch_default_key,
                    visiting=nested_visiting,
                )
            )
        return tuple(leaves)
    if isinstance(decoded, MultiMesh):
        leaves = []
        nested_visiting = visiting | {visit_key}
        for part_index, multi_part in enumerate(decoded.parts):
            leaves.extend(
                _resolve_mesh_leaves(
                    decoder,
                    multi_part.mesh,
                    result,
                    external_resolver=external_resolver,
                    path=(*path, part_index),
                    lod_distance=lod_distance,
                    lod_max_distance=lod_max_distance,
                    lod_part_index=lod_part_index,
                    wrapper_transforms=(
                        *wrapper_transforms,
                        multi_part.local_transform,
                    ),
                    switch_key=switch_key,
                    switch_default_key=switch_default_key,
                    visiting=nested_visiting,
                )
            )
        return tuple(leaves)
    if isinstance(decoded, SwitchMesh):
        leaves = []
        nested_visiting = visiting | {visit_key}
        for part_index, switch_part in enumerate(decoded.parts):
            leaves.extend(
                _resolve_mesh_leaves(
                    decoder,
                    switch_part.mesh,
                    result,
                    external_resolver=external_resolver,
                    path=(*path, part_index),
                    lod_distance=lod_distance,
                    lod_max_distance=lod_max_distance,
                    lod_part_index=lod_part_index,
                    wrapper_transforms=wrapper_transforms,
                    switch_key=switch_part.key,
                    switch_default_key=decoded.default_key,
                    visiting=nested_visiting,
                )
            )
        return tuple(leaves)
    result.warnings.append(
        f"Mesh resource object {index} has unsupported wrapper type "
        f"{record.type_name}"
    )
    return ()


def _static_mesh_templates(
    leaf: _MeshLeaf,
    options: ImportOptions,
    result: ImportResult,
    material_builder: MaterialBuilder | None,
    external_resolver: PsarcResourceResolver | None,
    cache: dict[tuple[int, int, int], _MeshTemplate],
    coordinate_basis: Matrix | None,
) -> tuple[_MeshTemplate, ...]:
    resolved: list[_MeshTemplate] = []
    for primitive_index, reference in enumerate(leaf.mesh.primitives):
        key = (id(leaf.decoder.core), leaf.record.index, primitive_index)
        existing = cache.get(key)
        if existing is not None:
            resolved.append(existing)
            continue
        primitive_resource = _resolve_reference(
            leaf.decoder,
            reference,
            external_resolver,
        )
        if primitive_resource is None or not isinstance(
            primitive_resource.value,
            Primitive,
        ):
            result.warnings.append(
                f"{leaf.record.type_name} {leaf.record.display_id} primitive "
                f"{primitive_index} is unresolved or unsupported"
            )
            continue
        primitive = primitive_resource.value
        primitive_decoder = primitive_resource.decoder
        vertex_resource = _resolve_reference(
            primitive_decoder,
            primitive.vertex_array,
            external_resolver,
        )
        index_resource = _resolve_reference(
            primitive_decoder,
            primitive.index_array,
            external_resolver,
        )
        if (
            vertex_resource is None
            or not isinstance(vertex_resource.value, VertexArray)
            or index_resource is None
            or not isinstance(index_resource.value, IndexArray)
        ):
            result.warnings.append(
                f"{leaf.record.type_name} {leaf.record.display_id} primitive "
                f"{primitive_index} has unresolved buffers"
            )
            continue
        name = leaf.mesh.prefix.name or (
            f"{leaf.record.type_name}_{leaf.record.index}"
        )
        mesh_data = _create_mesh_data(
            f"{name}_P{primitive_index:02d}",
            primitive,
            vertex_resource.value,
            index_resource.value,
            options,
            static_position_transform=leaf.mesh.local_translate_scale,
            coordinate_basis=coordinate_basis,
        )
        mesh_data["kz2_object_index"] = leaf.record.index
        mesh_data["kz2_object_id"] = leaf.record.display_id
        mesh_data["kz2_type"] = leaf.record.type_name
        if material_builder is not None:
            material = material_builder.material_for(
                primitive.render_effect,
                decoder=primitive_decoder,
            )
            if material is not None:
                mesh_data.materials.append(material)
        template = _MeshTemplate(
            mesh_data,
            primitive.render_effect,
            primitive_decoder,
        )
        cache[key] = template
        resolved.append(template)
    return tuple(resolved)


def _skinned_mesh_templates(
    decoder: CoreDecoder,
    record: CoreObject,
    mesh: SkinnedMesh,
    options: ImportOptions,
    result: ImportResult,
    material_builder: MaterialBuilder | None,
    external_resolver: PsarcResourceResolver | None,
    cache: dict[tuple[int, int, int], _SkinnedMeshTemplate],
    coordinate_basis: Matrix | None,
) -> tuple[_SkinnedMeshTemplate, ...]:
    skin_info_resource = _resolve_reference(
        decoder,
        mesh.skin_info,
        external_resolver,
    )
    if skin_info_resource is None or not isinstance(
        skin_info_resource.value,
        SkinInfo,
    ):
        result.warnings.append(
            f"Skinned mesh {record.display_id} has unresolved skin data"
        )
        return ()
    skin_info = skin_info_resource.value
    bindings_resource = _resolve_reference(
        decoder,
        mesh.bone_bindings,
        external_resolver,
    )
    bone_bindings = (
        bindings_resource.value
        if bindings_resource is not None
        and isinstance(bindings_resource.value, BoneBindings)
        else None
    )
    if bone_bindings is None:
        result.warnings.append(
            f"Skinned mesh {record.display_id} has unresolved bone bindings"
        )
    skeleton_resource = _resolve_reference(
        decoder,
        mesh.skeleton,
        external_resolver,
    )
    skeleton_decoder = None
    skeleton_record = None
    skeleton = None
    if skeleton_resource is not None and isinstance(
        skeleton_resource.value,
        Skeleton,
    ):
        skeleton_decoder = skeleton_resource.decoder
        skeleton_record = skeleton_resource.record
        skeleton = skeleton_resource.value
    else:
        result.warnings.append(
            f"Skinned mesh {record.display_id} has unresolved skeleton"
        )

    templates: list[_SkinnedMeshTemplate] = []
    for primitive_index, reference in enumerate(mesh.primitives):
        key = (id(decoder.core), record.index, primitive_index)
        existing = cache.get(key)
        if existing is not None:
            templates.append(existing)
            continue
        if primitive_index >= len(skin_info.parts):
            result.warnings.append(
                f"Skinned mesh {record.display_id} has no skin part "
                f"for primitive {primitive_index}"
            )
            continue
        primitive_resource = _resolve_reference(
            decoder,
            reference,
            external_resolver,
        )
        if primitive_resource is None or not isinstance(
            primitive_resource.value,
            Primitive,
        ):
            result.warnings.append(
                f"Skinned mesh {record.display_id} primitive "
                f"{primitive_index} is unresolved"
            )
            continue
        primitive = primitive_resource.value
        vertex_resource = _resolve_reference(
            primitive_resource.decoder,
            primitive.vertex_array,
            external_resolver,
        )
        index_resource = _resolve_reference(
            primitive_resource.decoder,
            primitive.index_array,
            external_resolver,
        )
        if (
            vertex_resource is None
            or not isinstance(vertex_resource.value, VertexArray)
            or index_resource is None
            or not isinstance(index_resource.value, IndexArray)
        ):
            result.warnings.append(
                f"Skinned mesh {record.display_id} primitive "
                f"{primitive_index} has unresolved buffers"
            )
            continue
        skin_vertices = skin_info.parts[primitive_index].vertices
        if not skin_vertices:
            result.warnings.append(
                f"Skinned mesh {record.display_id} primitive "
                f"{primitive_index} has no skin vertices"
            )
            continue
        positions = tuple(
            reconstruct_skin_position(
                vertex,
                mesh.position_bounds_scale,
                mesh.position_bounds_offset,
            )
            for vertex in skin_vertices
        )
        normals = tuple(vertex.normal for vertex in skin_vertices)
        name = mesh.prefix.name or f"{record.type_name}_{record.index}"
        mesh_data = _create_mesh_data(
            f"{name}_P{primitive_index:02d}",
            primitive,
            vertex_resource.value,
            index_resource.value,
            options,
            position_override=positions,
            normal_override=normals,
            coordinate_basis=coordinate_basis,
        )
        mesh_data["kz2_object_index"] = record.index
        mesh_data["kz2_object_id"] = record.display_id
        mesh_data["kz2_type"] = record.type_name
        effect_reference = primitive.render_effect
        effect_decoder = primitive_resource.decoder
        if primitive_index < len(mesh.render_effects):
            effect_reference = mesh.render_effects[primitive_index]
            effect_decoder = decoder
        if material_builder is not None:
            material = material_builder.material_for(
                effect_reference,
                decoder=effect_decoder,
            )
            if material is not None:
                mesh_data.materials.append(material)
        template = _SkinnedMeshTemplate(
            mesh_data,
            effect_reference,
            effect_decoder,
            skin_vertices,
            bone_bindings,
            skeleton_decoder,
            skeleton_record,
            skeleton,
        )
        cache[key] = template
        templates.append(template)
    return tuple(templates)


def _bind_skinned_template(
    context: bpy.types.Context | None,
    collection: bpy.types.Collection,
    placement: bpy.types.Object,
    mesh_object: bpy.types.Object,
    template: _SkinnedMeshTemplate,
    options: ImportOptions,
    result: ImportResult,
    armatures: dict[tuple[int, int], _EntityArmature],
) -> None:
    if template.bone_bindings is not None:
        _add_skin_weights(
            mesh_object,
            template.skin_vertices,
            template.bone_bindings,
            result,
        )
    if (
        context is None
        or template.skeleton_decoder is None
        or template.skeleton_record is None
        or template.skeleton is None
        or not (options.import_skeletons or options.import_skinned_meshes)
    ):
        mesh_object.parent = placement
        return

    key = (
        id(template.skeleton_decoder.core),
        template.skeleton_record.index,
    )
    target = armatures.get(key)
    if target is None:
        armature_object = _create_armature(
            context,
            collection,
            template.skeleton_record,
            template.skeleton,
            options,
        )
        armature_object.parent = placement
        armature_object.matrix_local = Matrix.Identity(4)
        armature_object["kz2_cached_entity_armature"] = True
        target = _EntityArmature(
            armature_object,
            template.skeleton_decoder,
            template.skeleton_record,
            template.skeleton,
        )
        armatures[key] = target
        result.armatures += 1
    modifier = mesh_object.modifiers.new(
        name="KZ2 Armature",
        type="ARMATURE",
    )
    modifier.object = target.object
    mesh_object.parent = target.object


def _apply_instance_material_overrides(
    mesh_object: bpy.types.Object,
    render_effect: CoreReference,
    render_effect_decoder: CoreDecoder,
    instance: StaticMeshInstance,
    leaf: _MeshLeaf | _SkinnedMeshLeaf,
    instance_index: int,
    material_builder: MaterialBuilder | None,
) -> None:
    if material_builder is None:
        return
    for mesh_override in instance.shader_overrides.mesh_overrides:
        if (
            mesh_override.mesh != instance.static_model
            or mesh_override.hierarchy_path != leaf.wrapper_path
        ):
            continue
        for effect_override in mesh_override.render_effect_overrides:
            if effect_override.render_effect != render_effect:
                continue
            material = material_builder.material_with_overrides(
                render_effect,
                effect_override.texture_overrides,
                effect_override.variable_overrides,
                instance_key=instance_index,
                decoder=render_effect_decoder,
            )
            if material is not None and mesh_object.material_slots:
                slot = mesh_object.material_slots[0]
                slot.link = "OBJECT"
                slot.material = material
            return


def _map_coordinate_basis() -> Matrix:
    return Matrix(
        (
            (1.0, 0.0, 0.0),
            (0.0, 0.0, -1.0),
            (0.0, 1.0, 0.0),
        )
    )


def _level_matrix(
    values,
    options: ImportOptions,
) -> Matrix:
    matrix = Matrix(values).transposed()
    if options.convert_map_axes:
        basis = _map_coordinate_basis().to_4x4()
        matrix = basis @ matrix @ basis.inverted()
    matrix.translation *= options.global_scale
    return matrix


def _wrapper_matrix(
    transforms: tuple[Matrix4, ...],
    options: ImportOptions,
) -> Matrix:
    result = Matrix.Identity(4)
    for transform in transforms:
        result = result @ _level_matrix(transform, options)
    return result


def _create_static_mesh(
    collection: bpy.types.Collection,
    decoder: CoreDecoder,
    record: CoreObject,
    mesh: StaticMesh,
    options: ImportOptions,
    result: ImportResult,
    material_builder: MaterialBuilder | None,
) -> int:
    created = 0
    for primitive_index, reference in enumerate(mesh.primitives):
        primitive = decoder.resolve(reference)
        if not isinstance(primitive, Primitive):
            result.warnings.append(
                f"{record.type_name} {record.display_id} primitive "
                f"{primitive_index} is unresolved or unsupported"
            )
            continue
        vertex_array = decoder.resolve(primitive.vertex_array)
        index_array = decoder.resolve(primitive.index_array)
        if not isinstance(vertex_array, VertexArray) or not isinstance(
            index_array,
            IndexArray,
        ):
            result.warnings.append(
                f"{record.type_name} {record.display_id} primitive "
                f"{primitive_index} has unresolved buffers"
            )
            continue
        name = mesh.prefix.name or f"{record.type_name}_{record.index}"
        mesh_object = _create_mesh_object(
            collection,
            f"{name}_P{primitive_index:02d}",
            record,
            primitive,
            vertex_array,
            index_array,
            options,
            static_position_transform=mesh.local_translate_scale,
        )
        if material_builder is not None:
            material_builder.assign(mesh_object, primitive.render_effect)
        created += 1
    return created


def _create_skinned_mesh(
    collection: bpy.types.Collection,
    decoder: CoreDecoder,
    record: CoreObject,
    mesh: SkinnedMesh,
    armatures: dict[int, bpy.types.Object],
    options: ImportOptions,
    result: ImportResult,
    material_builder: MaterialBuilder | None,
) -> int:
    skin_info = decoder.resolve(mesh.skin_info)
    bindings = decoder.resolve(mesh.bone_bindings)
    armature = (
        armatures.get(mesh.skeleton.local_index)
        if mesh.skeleton.local_index is not None
        else None
    )
    if not isinstance(skin_info, SkinInfo):
        result.warnings.append(
            f"Skinned mesh {record.display_id} has unresolved skin data"
        )
        return 0
    if not isinstance(bindings, BoneBindings):
        result.warnings.append(
            f"Skinned mesh {record.display_id} has unresolved bone bindings"
        )
        return 0
    if armature is None:
        result.warnings.append(
            f"Skinned mesh {record.display_id} has no local skeleton armature"
        )

    created = 0
    for primitive_index, reference in enumerate(mesh.primitives):
        if primitive_index >= len(skin_info.parts):
            result.warnings.append(
                f"Skinned mesh {record.display_id} has no skin part "
                f"for primitive {primitive_index}"
            )
            continue
        primitive = decoder.resolve(reference)
        if not isinstance(primitive, Primitive):
            result.warnings.append(
                f"Skinned mesh {record.display_id} primitive "
                f"{primitive_index} is unresolved"
            )
            continue
        vertex_array = decoder.resolve(primitive.vertex_array)
        index_array = decoder.resolve(primitive.index_array)
        if not isinstance(vertex_array, VertexArray) or not isinstance(
            index_array,
            IndexArray,
        ):
            result.warnings.append(
                f"Skinned mesh {record.display_id} primitive "
                f"{primitive_index} has unresolved buffers"
            )
            continue

        skin_vertices = skin_info.parts[primitive_index].vertices
        if not skin_vertices:
            result.warnings.append(
                f"Skinned mesh {record.display_id} primitive "
                f"{primitive_index} has no skin vertices"
            )
            continue
        positions = tuple(
            reconstruct_skin_position(
                vertex,
                mesh.position_bounds_scale,
                mesh.position_bounds_offset,
            )
            for vertex in skin_vertices
        )
        normals = tuple(vertex.normal for vertex in skin_vertices)
        name = mesh.prefix.name or f"{record.type_name}_{record.index}"
        obj = _create_mesh_object(
            collection,
            f"{name}_P{primitive_index:02d}",
            record,
            primitive,
            vertex_array,
            index_array,
            options,
            position_override=positions,
            normal_override=normals,
        )
        _add_skin_weights(obj, skin_vertices, bindings, result)
        if armature is not None:
            modifier = obj.modifiers.new(name="KZ2 Armature", type="ARMATURE")
            modifier.object = armature
            obj.parent = armature
        if material_builder is not None:
            effect_reference = (
                mesh.render_effects[primitive_index]
                if primitive_index < len(mesh.render_effects)
                else primitive.render_effect
            )
            material_builder.assign(obj, effect_reference)
        created += 1
    return created


def _create_mesh_object(
    collection: bpy.types.Collection,
    name: str,
    record: CoreObject,
    primitive: Primitive,
    vertex_array: VertexArray,
    index_array: IndexArray,
    options: ImportOptions,
    *,
    position_override: tuple[tuple[float, float, float], ...] | None = None,
    normal_override: tuple[tuple[float, float, float], ...] | None = None,
    static_position_transform: tuple[float, float, float, float] | None = None,
) -> bpy.types.Object:
    mesh_data = _create_mesh_data(
        name,
        primitive,
        vertex_array,
        index_array,
        options,
        position_override=position_override,
        normal_override=normal_override,
        static_position_transform=static_position_transform,
    )
    obj = bpy.data.objects.new(name, mesh_data)
    collection.objects.link(obj)
    _set_source_properties(obj, record)
    return obj


def _create_mesh_data(
    name: str,
    primitive: Primitive,
    vertex_array: VertexArray,
    index_array: IndexArray,
    options: ImportOptions,
    *,
    position_override: tuple[tuple[float, float, float], ...] | None = None,
    normal_override: tuple[tuple[float, float, float], ...] | None = None,
    static_position_transform: tuple[float, float, float, float] | None = None,
    coordinate_basis: Matrix | None = None,
) -> bpy.types.Mesh:
    positions = position_override
    if positions is None:
        decoded_positions = decode_semantic(vertex_array, VertexSemantic.POSITION)
        if decoded_positions is None:
            decoded_positions = decode_semantic(vertex_array, VertexSemantic.POSITION4)
        if decoded_positions is None:
            raise ValueError(f"{name}: vertex array has no position declaration")
        positions = tuple(
            (value[0], value[1], value[2]) for value in decoded_positions
        )
        if static_position_transform is not None:
            positions = tuple(
                reconstruct_static_position(
                    position,
                    static_position_transform,
                )
                for position in positions
            )

    if coordinate_basis is None:
        scaled = tuple(
            tuple(component * options.global_scale for component in position)
            for position in positions
        )
    else:
        scaled = tuple(
            tuple(
                coordinate_basis
                @ Vector(position)
                * options.global_scale
            )
            for position in positions
        )
    indexes = tuple(index + primitive.base_vertex for index in index_array.indexes)
    if indexes and max(indexes) >= len(scaled):
        raise ValueError(
            f"{name}: index {max(indexes)} exceeds {len(scaled)} vertices"
        )
    faces = [
        (indexes[index], indexes[index + 1], indexes[index + 2])
        for index in range(0, len(indexes), 3)
    ]

    mesh_data = bpy.data.meshes.new(name)
    mesh_data.from_pydata(scaled, (), faces)
    mesh_data.update(calc_edges=True)

    normals = normal_override
    if normals is None:
        decoded_normals = decode_semantic(vertex_array, VertexSemantic.NORMAL)
        if decoded_normals is not None:
            normals = tuple(
                (value[0], value[1], value[2]) for value in decoded_normals
            )
    if normals is not None and len(normals) == len(mesh_data.vertices):
        if coordinate_basis is not None:
            normals = tuple(
                tuple(coordinate_basis @ Vector(normal)) for normal in normals
            )
        mesh_data.normals_split_custom_set_from_vertices(normals)

    for uv_index, semantic in enumerate(
        (
            VertexSemantic.UV0,
            VertexSemantic.UV1,
            VertexSemantic.UV2,
            VertexSemantic.UV3,
            VertexSemantic.UV4,
            VertexSemantic.UV5,
            VertexSemantic.UV6,
        )
    ):
        values = decode_semantic(vertex_array, semantic)
        if values is None:
            continue
        layer = mesh_data.uv_layers.new(name=f"UV{uv_index}")
        for loop in mesh_data.loops:
            source_uv, _ = blender_uv_pairs(
                values[loop.vertex_index],
                flip_v=options.flip_uv_v,
            )
            layer.data[loop.index].uv = source_uv
        if semantic != VertexSemantic.UV0:
            continue
        diffuse_layer = mesh_data.uv_layers.new(name="DiffuseUV")
        for loop in mesh_data.loops:
            _, diffuse_uv = blender_uv_pairs(
                values[loop.vertex_index],
                flip_v=options.flip_uv_v,
            )
            diffuse_layer.data[loop.index].uv = diffuse_uv
        mesh_data.uv_layers.active = diffuse_layer
        diffuse_layer.active_render = True
    return mesh_data


def _add_skin_weights(
    obj: bpy.types.Object,
    vertices,
    bindings: BoneBindings,
    result: ImportResult,
) -> None:
    groups = {
        index: obj.vertex_groups.new(name=name or f"Bone_{index:03d}")
        for index, name in enumerate(bindings.bone_names)
    }
    invalid: set[int] = set()
    for vertex_index, vertex in enumerate(vertices):
        for bone_index, weight in zip(
            vertex.bone_indices,
            vertex.weights,
            strict=True,
        ):
            if weight <= 0:
                continue
            group = groups.get(bone_index)
            if group is None:
                invalid.add(bone_index)
                continue
            group.add((vertex_index,), weight, "ADD")
    if invalid:
        result.warnings.append(
            f"{obj.name}: ignored out-of-range skin bone indices {sorted(invalid)}"
        )


def _create_armature(
    context: bpy.types.Context,
    collection: bpy.types.Collection,
    record: CoreObject,
    skeleton: Skeleton,
    options: ImportOptions,
) -> bpy.types.Object:
    name = skeleton.prefix.name or f"Skeleton_{record.index}"
    armature_data = bpy.data.armatures.new(name)
    armature_object = bpy.data.objects.new(name, armature_data)
    collection.objects.link(armature_object)
    _set_source_properties(armature_object, record)
    armature_object["kz2_joint_count"] = len(skeleton.joints)
    armature_object["kz2_global_scale"] = options.global_scale

    previous_active = context.view_layer.objects.active
    previously_selected = tuple(context.selected_objects)
    for selected in previously_selected:
        selected.select_set(False)
    armature_object.select_set(True)
    context.view_layer.objects.active = armature_object
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        world_matrices = _joint_world_matrices(skeleton, options.global_scale)
        edit_bones: dict[str, bpy.types.EditBone] = {}
        for joint, world in zip(skeleton.joints, world_matrices, strict=True):
            bone = armature_data.edit_bones.new(joint.name)
            bone.head = (0.0, 0.0, 0.0)
            bone.tail = (0.0, max(0.01, 0.05 * options.global_scale), 0.0)
            bone.matrix = world
            edit_bones[joint.name] = bone
        for joint in skeleton.joints:
            if joint.parent_name:
                edit_bones[joint.name].parent = edit_bones[joint.parent_name]
                edit_bones[joint.name].use_connect = False
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
        armature_object.select_set(False)
        for selected in previously_selected:
            if selected.name in context.view_layer.objects:
                selected.select_set(True)
        context.view_layer.objects.active = previous_active
    for joint_index, joint in enumerate(skeleton.joints):
        bone = armature_data.bones[joint.name]
        bone["kz2_joint_index"] = joint_index
        if skeleton.edge_joints:
            edge_joint = skeleton.edge_joints[joint_index]
            bone["kz2_animation_rotation_xyzw"] = edge_joint.rotation_xyzw
            bone["kz2_animation_translation"] = edge_joint.translation
            bone["kz2_animation_scale"] = edge_joint.scale
            bone["kz2_animation_parent_index"] = (
                -1
                if edge_joint.parent_index is None
                else edge_joint.parent_index
            )
    return armature_object


def _joint_local_matrix(joint, scale: float) -> Matrix:
    return _transform_local_matrix(
        joint.rotation_xyzw,
        joint.translation,
        (1.0, 1.0, 1.0),
        scale,
    )


def _transform_local_matrix(
    rotation_xyzw: Sequence[float],
    translation: Sequence[float],
    transform_scale: Sequence[float],
    global_scale: float,
) -> Matrix:
    if (
        len(rotation_xyzw) != 4
        or len(translation) != 3
        or len(transform_scale) != 3
    ):
        raise ValueError("invalid transform component dimensions")
    x, y, z, w = rotation_xyzw
    rotation = Quaternion((w, x, y, z)).normalized()
    matrix = rotation.to_matrix().to_4x4()
    matrix.translation = Vector(translation) * global_scale
    return matrix @ Matrix.Diagonal((*transform_scale, 1.0))


def _joint_world_matrices(skeleton: Skeleton, scale: float) -> tuple[Matrix, ...]:
    indices = {joint.name: index for index, joint in enumerate(skeleton.joints)}
    if len(indices) != len(skeleton.joints):
        raise ValueError("skeleton contains duplicate joint names")
    result: list[Matrix | None] = [None] * len(skeleton.joints)
    visiting: set[int] = set()

    def resolve(index: int) -> Matrix:
        existing = result[index]
        if existing is not None:
            return existing
        if index in visiting:
            raise ValueError("skeleton hierarchy contains a cycle")
        visiting.add(index)
        joint = skeleton.joints[index]
        local = _joint_local_matrix(joint, scale)
        if joint.parent_name:
            try:
                parent_index = indices[joint.parent_name]
            except KeyError as exc:
                raise ValueError(
                    f"joint {joint.name!r} has unknown parent "
                    f"{joint.parent_name!r}"
                ) from exc
            world = resolve(parent_index) @ local
        else:
            world = local
        visiting.remove(index)
        result[index] = world
        return world

    return tuple(resolve(index) for index in range(len(skeleton.joints)))


def _create_cached_entity_actions(
    context: bpy.types.Context,
    placement: bpy.types.Object,
    entity_decoder: CoreDecoder,
    armatures: dict[tuple[int, int], _EntityArmature],
    options: ImportOptions,
    result: ImportResult,
    external_resolver: PsarcResourceResolver,
) -> int:
    if not armatures:
        return 0

    decoders = {id(entity_decoder.core): entity_decoder}
    for target in armatures.values():
        decoders.setdefault(id(target.decoder.core), target.decoder)

    created = 0
    imported: set[tuple[int, int]] = set()
    user_channel_actions = 0
    skipped_user_channels = 0
    for decoder in decoders.values():
        state_names = animation_state_clip_names(decoder)
        for record in decoder.core.objects:
            if record.type_name != "SkeletonAnimationResource":
                continue
            key = (id(decoder.core), record.index)
            if key in imported:
                continue
            try:
                bone_map_reference = (
                    read_skeleton_animation_bone_map_reference(
                        decoder,
                        record,
                    )
                )
            except FormatError as exc:
                result.warnings.append(
                    f"Animation {record.display_id} header was skipped: {exc}"
                )
                continue
            target = _cached_animation_target(
                decoder,
                bone_map_reference,
                tuple(armatures.values()),
                external_resolver,
            )
            if target is None:
                continue
            try:
                decoded = decoder.decode(record)
            except FormatError as exc:
                result.warnings.append(
                    f"Animation {record.display_id} was skipped: {exc}"
                )
                continue
            if not isinstance(decoded, SkeletonAnimation):
                continue
            clip = decoded.clip
            if not isinstance(clip, EdgeAnimation):
                continue
            if not clip.decode_supported:
                result.warnings.append(clip.unsupported_reason)
                continue
            transform_channel_count = sum(clip.counts[:3]) + sum(
                clip.counts[4:7]
            )
            if transform_channel_count == 0:
                result.warnings.append(
                    f"Animation {record.display_id} contains only scalar/user "
                    "channels, which are not mapped to Blender properties"
                )
                continue

            labels = state_names.get(record.index, ())
            label = (
                labels[0]
                if labels
                else (
                    decoded.prefix.name
                    if decoded.prefix is not None and decoded.prefix.name
                    else f"EA02_{record.index}_{record.display_id[:8]}"
                )
            )
            action = _create_action(
                target.object,
                _action_joints(
                    target.object,
                    target.skeleton,
                    options.global_scale,
                ),
                record,
                decoded,
                float(
                    target.object.get(
                        "kz2_global_scale",
                        options.global_scale,
                    )
                ),
                context.scene.render.fps / context.scene.render.fps_base,
                name=f"{placement.name} - {label}",
            )
            action["kz2_cached_entity"] = True
            action["kz2_entity_name"] = placement.name
            action["kz2_state_names"] = list(labels)
            action["kz2_target_armature"] = target.object.name
            action["kz2_bone_map_verified"] = True
            imported.add(key)
            created += 1
            _LOG.info(
                "Created Action %s for %s (%d frames at %.3f Hz)",
                action.name,
                target.object.name,
                clip.frame_count,
                clip.sample_frequency,
            )
            user_channel_count = clip.counts[3] + clip.counts[7]
            if user_channel_count:
                user_channel_actions += 1
                skipped_user_channels += user_channel_count
    if skipped_user_channels:
        result.warnings.append(
            f"Imported transform tracks for {user_channel_actions} Actions "
            f"and skipped {skipped_user_channels} scalar/user channels that "
            "are not mapped to Blender properties"
        )
    return created


def _cached_animation_target(
    decoder: CoreDecoder,
    bone_map_reference: CoreReference,
    targets: tuple[_EntityArmature, ...],
    external_resolver: PsarcResourceResolver,
) -> _EntityArmature | None:
    bone_map_resource = _resolve_reference(
        decoder,
        bone_map_reference,
        external_resolver,
    )
    if (
        bone_map_resource is None
        or not isinstance(
            bone_map_resource.value,
            SkeletonAnimationBoneMap,
        )
    ):
        return None
    bone_names = bone_map_resource.value.bone_names
    matches = [
        target
        for target in targets
        if bone_names
        == tuple(joint.name for joint in target.skeleton.joints)
    ]
    return matches[0] if len(matches) == 1 else None


def _find_matching_skeleton(
    clip: EdgeAnimation,
    skeletons: dict[int, Skeleton],
) -> int | None:
    matches = [
        index
        for index, skeleton in skeletons.items()
        if len(skeleton.joints) == clip.joint_count
    ]
    return matches[0] if len(matches) == 1 else None


def _action_joints(
    armature_object: bpy.types.Object,
    skeleton: Skeleton | None,
    fallback_scale: float,
) -> tuple[_ActionJoint, ...]:
    if skeleton is not None:
        if len(skeleton.edge_joints) != len(skeleton.joints):
            raise ValueError(
                f"Skeleton {skeleton.prefix.name!r} lacks matching ES00 "
                "animation fallback transforms"
            )
        scale = float(armature_object.get("kz2_global_scale", fallback_scale))
        indices = {
            joint.name: index for index, joint in enumerate(skeleton.joints)
        }
        return tuple(
            _ActionJoint(
                joint.name,
                _joint_local_matrix(joint, scale),
                indices[joint.parent_name] if joint.parent_name else None,
                _transform_local_matrix(
                    edge_joint.rotation_xyzw,
                    edge_joint.translation,
                    edge_joint.scale,
                    scale,
                ),
                edge_joint.parent_index,
            )
            for joint, edge_joint in zip(
                skeleton.joints,
                skeleton.edge_joints,
                strict=True,
            )
        )

    indexed_bones = []
    for bone in armature_object.data.bones:
        if "kz2_joint_index" not in bone:
            raise ValueError(
                f"Armature {armature_object.name!r} lacks Killzone 2 joint-order "
                "metadata; re-import its skeleton before importing animations"
            )
        indexed_bones.append((int(bone["kz2_joint_index"]), bone))
    indexed_bones.sort(key=lambda item: item[0])
    expected = list(range(len(indexed_bones)))
    observed = [index for index, _ in indexed_bones]
    if observed != expected:
        raise ValueError(
            f"Armature {armature_object.name!r} has invalid Killzone 2 "
            f"joint-order metadata: {observed}"
        )

    bone_indices = {
        bone.name: index
        for index, (_, bone) in enumerate(indexed_bones)
    }
    scale = float(armature_object.get("kz2_global_scale", fallback_scale))
    joints = []
    for _, bone in indexed_bones:
        required_properties = (
            "kz2_animation_rotation_xyzw",
            "kz2_animation_translation",
            "kz2_animation_scale",
            "kz2_animation_parent_index",
        )
        missing = [
            property_name
            for property_name in required_properties
            if property_name not in bone
        ]
        if missing:
            raise ValueError(
                f"Armature {armature_object.name!r} lacks ES00 animation "
                f"metadata {missing}; re-import its skeleton"
            )
        rest_local = bone.matrix_local.copy()
        if bone.parent is not None:
            rest_local = bone.parent.matrix_local.inverted_safe() @ rest_local
        animation_parent = int(bone["kz2_animation_parent_index"])
        joints.append(
            _ActionJoint(
                bone.name,
                rest_local,
                (
                    bone_indices[bone.parent.name]
                    if bone.parent is not None
                    else None
                ),
                _transform_local_matrix(
                    bone["kz2_animation_rotation_xyzw"],
                    bone["kz2_animation_translation"],
                    bone["kz2_animation_scale"],
                    scale,
                ),
                None if animation_parent < 0 else animation_parent,
            )
        )
    return tuple(joints)


def _create_action(
    armature_object: bpy.types.Object,
    joints: tuple[_ActionJoint, ...],
    record: CoreObject,
    animation: SkeletonAnimation,
    global_scale: float,
    scene_fps: float,
    *,
    name: str | None = None,
) -> bpy.types.Action:
    clip = animation.clip
    if not isinstance(clip, EdgeAnimation):
        raise TypeError("expected a decoded EdgeAnimation")
    action_name = name or (
        animation.prefix.name
        if animation.prefix is not None and animation.prefix.name
        else f"EA02_{record.index}_{record.display_id[:8]}"
    )
    action = bpy.data.actions.new(action_name)
    action.use_fake_user = True
    slot = action.slots.new(id_type="OBJECT", name=armature_object.name)
    layer = action.layers.new(name="KZ2")
    strip = layer.strips.new(type="KEYFRAME")
    channelbag = strip.channelbag(slot, ensure=True)

    armature_object.animation_data_create()
    armature_object.animation_data.action = action
    armature_object.animation_data.action_slot = slot

    if not joints or len(joints) != clip.joint_count:
        raise ValueError(
            f"animation has {clip.joint_count} joints but target armature has "
            f"{len(joints)}"
        )
    if scene_fps <= 0.0:
        raise ValueError(f"scene frame rate must be positive, got {scene_fps}")
    animation_default_components = tuple(
        joint.animation_default_local.decompose() for joint in joints
    )
    paths = []
    curves: dict[tuple[int, str, int], bpy.types.FCurve] = {}
    for joint_index, joint in enumerate(joints):
        escaped = bpy.utils.escape_identifier(joint.name)
        base = f'pose.bones["{escaped}"]'
        paths.append(base)
        for property_name, size in (
            ("location", 3),
            ("rotation_quaternion", 4),
            ("scale", 3),
        ):
            for component in range(size):
                curves[(joint_index, property_name, component)] = (
                    channelbag.fcurves.ensure(
                        data_path=f"{base}.{property_name}",
                        index=component,
                        group_name=joint.name,
                    )
                )

    values: dict[tuple[int, str, int], list[float]] = {
        key: [] for key in curves
    }
    frames: list[float] = []
    previous_quaternions: list[Quaternion | None] = [None] * len(joints)
    for frame_index, samples in clip.iter_frames():
        blender_frame = 1.0 + frame_index * scene_fps / clip.sample_frequency
        frames.append(blender_frame)
        animated_local = []
        for sample, default_component in zip(
            samples,
            animation_default_components,
            strict=True,
        ):
            default_translation, default_rotation, default_scale = default_component
            rotation = (
                Quaternion(
                    (
                        sample.rotation_xyzw[3],
                        sample.rotation_xyzw[0],
                        sample.rotation_xyzw[1],
                        sample.rotation_xyzw[2],
                    )
                ).normalized()
                if sample.rotation_xyzw is not None
                else default_rotation
            )
            translation = (
                Vector(sample.translation) * global_scale
                if sample.translation is not None
                else default_translation
            )
            scale = (
                Vector(sample.scale)
                if sample.scale is not None
                else default_scale
            )
            animated_local.append(
                Matrix.Translation(translation)
                @ rotation.to_matrix().to_4x4()
                @ Matrix.Diagonal((*scale, 1.0))
            )
        animated_world = _hierarchical_world_matrices(
            tuple(animated_local),
            tuple(joint.animation_parent_index for joint in joints),
            "ES00 animation",
        )
        for joint_index, joint in enumerate(joints):
            parent_world = (
                Matrix.Identity(4)
                if joint.parent_index is None
                else animated_world[joint.parent_index]
            )
            target_local = (
                parent_world.inverted_safe() @ animated_world[joint_index]
            )
            basis = joint.rest_local.inverted_safe() @ target_local
            location, quaternion, pose_scale = basis.decompose()
            previous = previous_quaternions[joint_index]
            if previous is not None and previous.dot(quaternion) < 0:
                quaternion.negate()
            previous_quaternions[joint_index] = quaternion.copy()

            for component in range(3):
                values[(joint_index, "location", component)].append(
                    location[component]
                )
                values[(joint_index, "scale", component)].append(
                    pose_scale[component]
                )
            for component in range(4):
                values[(joint_index, "rotation_quaternion", component)].append(
                    quaternion[component]
                )

    for key, fcurve in curves.items():
        curve_values = values[key]
        fcurve.keyframe_points.add(len(frames))
        coordinates = [
            coordinate
            for frame, value in zip(frames, curve_values, strict=True)
            for coordinate in (frame, value)
        ]
        fcurve.keyframe_points.foreach_set("co", coordinates)
        for point in fcurve.keyframe_points:
            point.interpolation = "LINEAR"
        fcurve.update()

    action.frame_start = frames[0]
    action.frame_end = frames[-1]
    action.use_frame_range = True
    action["kz2_source_object"] = record.index
    action["kz2_source_id"] = record.display_id
    action["kz2_sample_frequency"] = clip.sample_frequency
    action["kz2_duration"] = clip.duration
    action["kz2_native_frame_count"] = clip.frame_count
    action["kz2_scene_frame_step"] = scene_fps / clip.sample_frequency
    action["kz2_frame_start"] = frames[0]
    action["kz2_frame_end"] = frames[-1]
    return action


def _hierarchical_world_matrices(
    local_matrices: tuple[Matrix, ...],
    parent_indices: tuple[int | None, ...],
    label: str,
) -> tuple[Matrix, ...]:
    if len(local_matrices) != len(parent_indices):
        raise ValueError(
            f"{label} transform and parent counts differ: "
            f"{len(local_matrices)} != {len(parent_indices)}"
        )
    result: list[Matrix | None] = [None] * len(local_matrices)
    visiting: set[int] = set()

    def resolve(index: int) -> Matrix:
        existing = result[index]
        if existing is not None:
            return existing
        if index in visiting:
            raise ValueError(f"{label} hierarchy contains a cycle at {index}")
        visiting.add(index)
        parent = parent_indices[index]
        if parent is not None and not 0 <= parent < len(local_matrices):
            raise ValueError(
                f"{label} parent index {parent} is invalid for joint {index}"
            )
        world = (
            local_matrices[index]
            if parent is None
            else resolve(parent) @ local_matrices[index]
        )
        visiting.remove(index)
        result[index] = world
        return world

    return tuple(resolve(index) for index in range(len(local_matrices)))


def _set_source_properties(
    obj: bpy.types.Object,
    record: CoreObject,
) -> None:
    obj["kz2_object_index"] = record.index
    obj["kz2_object_id"] = record.display_id
    obj["kz2_type"] = record.type_name
