// Mirrors backend/core/local_pipeline.py AGENTS — keys must match exactly.
export interface AgentDef {
  key: string;
  num: number;
  label: string;
  icon: string;
  desc: string;
  color: string;
}

export const AGENTS: AgentDef[] = [
  { key: "ingestion", num: 1, label: "Video Ingestion", icon: "🎬", desc: "Validates clips, probes metadata, generates thumbnails", color: "#3949ab" },
  { key: "scene_detection", num: 2, label: "Scene Detection", icon: "🎞", desc: "Shot boundaries, keyframes, quality scoring", color: "#f5a623" },
  { key: "speech_analysis", num: 3, label: "Speech Analysis", icon: "🗣", desc: "WhisperX transcription, diarization, filler detection", color: "#00897b" },
  { key: "face_detection", num: 4, label: "Face Detection", icon: "👤", desc: "InsightFace tracking + face-quality scoring", color: "#e91e8c" },
  { key: "emotion_analysis", num: 5, label: "Emotion Analysis", icon: "😊", desc: "Per-frame emotion + audio sentiment peaks", color: "#9c27b0" },
  { key: "story_builder", num: 6, label: "Story Builder", icon: "📖", desc: "Claude builds the narrative arc & beat order", color: "#1976d2" },
  { key: "editing_decision", num: 7, label: "Editing Decision", icon: "✂️", desc: "Claude → frame-accurate Edit Decision List", color: "#ff5722" },
  { key: "audio_enhancement", num: 8, label: "Audio Enhancement", icon: "🔊", desc: "Noise reduction, loudness, music ducking", color: "#4caf50" },
  { key: "subtitle", num: 9, label: "Subtitle", icon: "📝", desc: "Word-level subtitles, SRT/VTT export", color: "#ffc107" },
  { key: "rendering", num: 10, label: "Rendering", icon: "🎥", desc: "FFmpeg render, color grade, multi-format", color: "#7c4dff" },
  { key: "quality_assurance", num: 11, label: "Quality Assurance", icon: "✅", desc: "VMAF scoring, A/V sync, artifact checks", color: "#00acc1" },
];

export const FORMAT_META: Record<string, { label: string; ratio: string; res: string }> = {
  youtube: { label: "YouTube", ratio: "16:9", res: "1920×1080" },
  shorts: { label: "Shorts", ratio: "9:16", res: "1080×1920" },
  reels: { label: "Reels", ratio: "9:16", res: "1080×1920" },
  tiktok: { label: "TikTok", ratio: "9:16", res: "1080×1920" },
  linkedin: { label: "LinkedIn", ratio: "16:9", res: "1920×1080" },
};
