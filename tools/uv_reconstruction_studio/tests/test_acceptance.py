"""
Acceptance test (spec §100): the full workflow, minus the GUI-only
interaction steps (drawing guides with a mouse, opening panels by
clicking) which have no automated equivalent without a display -- this
exercises every *engine* of that workflow together: create project,
import texture, add guides, generate parts+queue, apply a material
preset, set padding, run reconstruction, reject and retry one part
with different settings, compare versions, mark another part as
"use original", reassemble, validate, export, and save the project.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from core.events.bus import EventBus
from core.events.types import EventType
from core.models.engine_models import ReconstructionRequest
from core.models.guide import Guide, GuideOrientation
from core.models.job import JobState
from core.models.part import PartStatus
from core.models.project import Project, SourceTexture
from engines.mock.mock_engine import MockEngine
from engines.registry import EngineRegistry
from pipeline.queue_manager import QueueManager
from project_system.manager import ProjectManager
from prompts.presets import PresetManager
from reassembly.assembler import reassemble
from reassembly.export import export_texture
from uv.parts_generator import generate_parts


class TestFullAcceptanceWorkflow(unittest.TestCase):
    def test_full_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)

            # 1. Create Project + Import PNG
            source_image = Image.new("RGBA", (128, 128), (30, 60, 90, 255))
            source_path = tmp_dir / "source.png"
            source_image.save(source_path)

            project = Project(name="Acceptance Test", source=SourceTexture(
                filename="source.png", width=128, height=128, format="PNG",
            ))
            working_dir = tmp_dir / "project"
            manager = ProjectManager()
            manager.create(project, working_dir)
            (working_dir / "texture" / "source.png").parent.mkdir(parents=True, exist_ok=True)
            source_image.save(working_dir / "texture" / "source.png")

            # 2. Draw horizontal + vertical guides
            project.add_guide(Guide(orientation=GuideOrientation.HORIZONTAL, position=64))
            project.add_guide(Guide(orientation=GuideOrientation.VERTICAL, position=64))

            # 3. Generate Parts / Generate Queue
            new_parts = generate_parts(project, source_image, working_dir, default_padding=8)
            self.assertEqual(len(new_parts), 4)

            bus = EventBus()
            events_seen = []
            bus.subscribe_all(events_seen.append)

            registry = EngineRegistry()
            registry.register("mock", lambda **cfg: MockEngine(event_bus=bus, total_steps=3, step_delay=0.0))

            def request_builder(part, job):
                version_index = len(part.history) + 1
                return ReconstructionRequest(
                    job_id=job.id, part_id=part.id,
                    input_path=str(working_dir / "parts" / part.id / "source" / "crop.png"),
                    output_path=str(working_dir / "parts" / part.id / "output" / f"mock_v{version_index:03d}.png"),
                    prompt=part.prompt, strength=part.strength,
                )

            queue = QueueManager(registry, request_builder, event_bus=bus)

            # 4. Open Part Inspector / set material, strength, prompt (first part)
            preset_manager = PresetManager()
            target_part = project.parts[0]
            preset_manager.apply_material_to_part(target_part, "Metal")
            target_part.prompt = "brushed metal panel"
            self.assertEqual(target_part.material, "Metal")

            # 5. Set padding already applied at generation (padding=8 above)
            self.assertEqual(target_part.padding, 8)

            for part in project.parts:
                queue.add(part, "mock", project_id=project.id)

            # 6. Run reconstruction -- monitor via events
            queue.start_all()
            self.assertTrue(queue.wait_idle(timeout=10))
            queue.shutdown()

            self.assertTrue(all(p.status == PartStatus.COMPLETED for p in project.parts))
            self.assertIn(EventType.JOB_COMPLETED, [e.type for e in events_seen])
            self.assertIn(EventType.JOB_PROGRESS, [e.type for e in events_seen])

            # 7. Reject one part, change strength, re-run just that part
            rejected_part = project.parts[1]
            original_version_count = len(rejected_part.history)
            rejected_part.strength = 0.9

            registry2 = EngineRegistry()
            registry2.register("mock", lambda **cfg: MockEngine(event_bus=bus, total_steps=2, step_delay=0.0))
            queue2 = QueueManager(registry2, request_builder, event_bus=bus)
            job = queue2.add(rejected_part, "mock", project_id=project.id)
            queue2.start_all()
            self.assertTrue(queue2.wait_idle(timeout=10))
            queue2.shutdown()

            self.assertEqual(job.state, JobState.COMPLETED)
            # 8. Compare versions -- there should now be two for this part
            self.assertEqual(len(rejected_part.history), original_version_count + 1)

            # 9. Use Original for another part (spec §91's explicit resolution path)
            using_original_part = project.parts[2]
            using_original_part.status = PartStatus.USING_ORIGINAL

            # 10. Reassemble + validate + export
            final_image = reassemble(source_image, project.parts, event_bus=bus)
            self.assertEqual(final_image.size, (128, 128))

            output_path = tmp_dir / "final_RECONSTRUCTED.png"
            export_texture(final_image, output_path)
            self.assertTrue(output_path.exists())
            with Image.open(output_path) as reopened:
                self.assertEqual(reopened.size, (128, 128))

            # 11. Save Project
            manager.save_as(working_dir)
            reloaded = ProjectManager().load(working_dir, working_dir)
            self.assertEqual(len(reloaded.parts), 4)
            self.assertEqual(sum(len(p.history) for p in reloaded.parts), sum(len(p.history) for p in project.parts))

            self.assertIn(EventType.REASSEMBLY_COMPLETED, [e.type for e in events_seen])


if __name__ == "__main__":
    unittest.main()
