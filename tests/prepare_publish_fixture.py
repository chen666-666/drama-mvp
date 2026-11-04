"""Create a dedicated public UI fixture, never read or write production data."""
import json
import argparse
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.test_publishing import PublishingTests
from services.media import executable, digest, validate
from services.publishing import ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-id", default="synthetic-ui")
    args = parser.parse_args()
    fixture = PublishingTests()
    fixture.setUp()
    try:
        subprocess.run([executable("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=navy:s=320x240:d=6",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=6", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        "-movflags", "+faststart", "-shortest", str(fixture.video)], check=True, capture_output=True)
        info = validate(fixture.video, 6)
        fixture.media.size = fixture.video.stat().st_size
        fixture.media.sha256 = digest(fixture.video)
        fixture.media.duration = info["duration"]
        from models import DownloadEpisode, DownloadJob, MediaFile
        second = DownloadEpisode(catalog_id=fixture.catalog.id, video_id="999000222", title="合成测试第二集", original_url="https://www.douyin.com/video/999000222",
                                 episode_number=2, status="approved", access="public")
        fixture.session.add(second); fixture.session.flush()
        job = DownloadJob(episode_id=second.id, status="verified")
        fixture.session.add(job); fixture.session.flush()
        second_path = fixture.video.with_name("第二集.mp4")
        second_path.write_bytes(fixture.video.read_bytes())
        fixture.session.add(MediaFile(job_id=job.id, relative_path=second_path.relative_to(fixture.data).as_posix(), size=second_path.stat().st_size,
            duration=info["duration"], format="mp4", sha256=digest(second_path), tracks="[]", validation_note="合成检查", manual_status="passed"))
        fixture.save_source()
        output = ROOT / "data/publishing-ui/runs" / args.release_id
        from services.publishing import build_bundle
        if (output / args.release_id).exists():
            raise SystemExit("专用页面测试包已存在；请使用现有包或人工归档后重新生成。")
        result = build_bundle(fixture.source, fixture.data, output, [fixture.ranking["id"]], preview=False, release_id=args.release_id, representatives_only=False)
        print(json.dumps({"fixture": str(output / args.release_id), "videos": result["video_count"]}))
    finally:
        fixture.tearDown()


if __name__ == "__main__":
    main()
