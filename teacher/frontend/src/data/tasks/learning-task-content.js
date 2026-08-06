import { publicAsset } from "../../public-assets.js";

const defaultLearningTaskContent = {
  firstViewRequiresCompletion: true,
  playbackRate: 1,
};

export function getLearningTaskContent(task) {
  const chapters = task.videoChapters || null;
  return {
    ...defaultLearningTaskContent,
    videoSrc: publicAsset(task.videoSrc) || null,
    chapters: chapters?.map((chapter) => ({
      ...chapter,
      videoSrc: publicAsset(chapter.videoSrc),
    })) || null,
    mock: Boolean(task.videoMock),
    poster: publicAsset(task.videoPoster) || defaultLearningTaskContent.poster,
  };
}
