import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = (path) => readFile(new URL(`../src/${path}`, import.meta.url), "utf8");

test("G01 keeps shared statuses and embeds its Kuozhi assessment", async () => {
  const profileTask = await source("features/task-content/ProfileCredentialsTask.jsx");

  assert.match(profileTask, /<ExternalStatusTask task=\{sourceStatusTask\} embedded \/>/);
  assert.match(profileTask, /<KuozhiCourseTask task=\{task\}/);
  assert.doesNotMatch(profileTask, /TESOL_QUIZ|quizQuestions|Submit answers/);
});

test("task completion views use the configured task reward", async () => {
  const [taskFlow, videoQuiz] = await Promise.all([
    source("components/TaskFlow.jsx"),
    source("features/task-content/VideoLearningTask.jsx"),
  ]);

  assert.match(taskFlow, /task\.tagReward/);
  assert.match(videoQuiz, /task\.tagReward/);
  assert.doesNotMatch(taskFlow, /courseAbilityTagForTask/);
  assert.doesNotMatch(videoQuiz, /courseAbilityTagForTask/);
});

test("video tasks keep the player display contract and support fullscreen playback", async () => {
  const videoQuiz = await source("features/task-content/VideoLearningTask.jsx");

  assert.match(videoQuiz, /poster=\{content\.poster\}/);
  assert.match(videoQuiz, /preload="auto"/);
  assert.match(videoQuiz, /lastSavedSecondsRef\.current \+ 10/);
  assert.match(videoQuiz, /onWaiting=\{\(event\) => \{/);
  assert.match(videoQuiz, /if \(!event\.currentTarget\.paused\) setBuffering\(true\)/);
  assert.match(videoQuiz, /"视频缓冲中…"/);
  assert.match(videoQuiz, /window\.addEventListener\("pagehide", persistCurrentPosition\)/);
  assert.match(videoQuiz, /video-fullscreen-button/);
  assert.match(videoQuiz, /player\.requestFullscreen/);
  assert.match(videoQuiz, /video\.webkitEnterFullscreen/);
  assert.match(videoQuiz, /"全屏播放"/);
  assert.match(videoQuiz, /"退出全屏"/);
});

test("completed video references keep a replay entry without changing completion status", async () => {
  const [taskFlow, videoQuiz, videoStyles] = await Promise.all([
    source("components/TaskFlow.jsx"),
    source("features/task-content/VideoLearningTask.jsx"),
    source("features/task-content/video-learning-task.css"),
  ]);

  assert.match(videoQuiz, /onReplayVideo/);
  assert.match(videoQuiz, /"回看视频"/);
  assert.match(videoQuiz, /"首次完整观看已完成 · 可拖动进度自由回看"/);
  assert.match(videoQuiz, /reviewMode/);
  assert.match(
    videoQuiz,
    /reviewMode \|\| task\.videoCompleted \|\| Number\(task\.videoProgress\) >= 100/,
  );
  assert.match(videoQuiz, /disabled=\{!firstViewComplete \|\| !duration\}/);
  assert.match(videoQuiz, /videoRef\.current\.currentTime = nextTime/);
  assert.match(videoQuiz, /setPointerCapture/);
  assert.match(videoQuiz, /onPointerMove=\{handleSeekPointerMove\}/);
  assert.match(videoQuiz, /firstViewComplete \? playbackProgress : videoProgress/);
  assert.match(videoQuiz, /width: `\$\{timelineProgress\}%`/);
  assert.match(videoQuiz, /const visualState = current[\s\S]*reviewMode[\s\S]*"reviewable"/);
  assert.match(videoQuiz, /disabled=\{!reviewMode\}/);
  assert.match(videoQuiz, /!reviewMode && completed \? <Check/);
  assert.doesNotMatch(videoQuiz, /disabled=\{!reviewMode \|\| !completed\}/);
  assert.match(videoStyles, /\.real-video-player \.video-progress[\s\S]*width: 100%/);
  assert.match(videoStyles, /video-seek-control::-webkit-slider-thumb/);
  assert.match(videoStyles, /\.video-seek-control:disabled[\s\S]*opacity: 0/);
  assert.match(videoStyles, /\.chapter-strip button\.reviewable/);
  assert.match(videoQuiz, /setActiveIndex\(index\)/);
  assert.match(taskFlow, /task\.status === "completed" && !reviewing/);
  assert.match(taskFlow, /onSecondary=\{hasVideo \? \(\) => setReviewing\(true\) : null\}/);
  assert.match(taskFlow, /c\("Replay video", "回看视频"\)/);
  assert.match(taskFlow, /task\.status === "completed" && reviewing/);
  assert.match(taskFlow, /<ChapterVideoLearning[\s\S]*reviewMode/);
});

test("video completion follows the saved backend step status", async () => {
  const [integratedTaskFlow, videoQuiz] = await Promise.all([
    source("components/IntegratedTaskFlow.jsx"),
    source("features/task-content/VideoLearningTask.jsx"),
  ]);

  assert.match(integratedTaskFlow, /refresh: refreshLatestContext/);
  assert.match(videoQuiz, /response\?\.step\?\.status !== "COMPLETED"/);
  assert.match(videoQuiz, /Math\.floor\(elapsed\) - 10/);
});

test("task details show the reason only once in the why-this-task section", async () => {
  const app = await source("App.jsx");

  assert.doesNotMatch(app, /className="task-intro"/);
  assert.match(app, /<h3>\{copy\(language, "Why this task", "为什么要做"\)\}<\/h3>/);
});

test("G04 keeps preparation confirmation at the very bottom without gating photo review", async () => {
  const [integratedTaskFlow, readinessPhoto] = await Promise.all([
    source("components/IntegratedTaskFlow.jsx"),
    source("features/task-content/ReadinessPhotoTask.jsx"),
  ]);

  assert.match(readinessPhoto, /disabled=\{opening\} onClick=\{openCamera\}/);
  assert.match(
    readinessPhoto,
    /disabled=\{!photoFile \|\| analyzing \|\| photoApproved\} onClick=\{submit\}/,
  );
  assert.doesNotMatch(readinessPhoto, /analyzing \|\| !coursewareConfirmed/);
  assert.doesNotMatch(
    readinessPhoto,
    /DEVICE_CHECK|devicePassed|deviceProgress|checkingDevice|runDeviceCheck/,
  );
  assert.match(integratedTaskFlow, /loadValidation: \(signal\) => getTaskValidation/);
  assert.match(readinessPhoto, /readinessPayloadFromValidation/);

  const captureIndex = readinessPhoto.indexOf('className="readiness-capture"');
  const submitIndex = readinessPhoto.indexOf('className="primary-button wide-button"');
  const privacyIndex = readinessPhoto.indexOf("readiness-privacy");
  const confirmationIndex = readinessPhoto.indexOf("readiness-preparation-confirm");
  assert.ok(captureIndex >= 0);
  assert.ok(submitIndex > captureIndex);
  assert.ok(privacyIndex > submitIndex);
  assert.ok(confirmationIndex > privacyIndex);
});

test("G04 uses Sophia's checklist first qualified photo as its camera reference", async () => {
  const readinessPhoto = await source("features/task-content/ReadinessPhotoTask.jsx");

  assert.match(
    readinessPhoto,
    /lesson-preparation-examples\/camera-angle-good-front\.jpg/,
  );
  assert.doesNotMatch(readinessPhoto, /self-intro-reference-51talk\.webp/);
});

test("G04 shows Sophia's four visual checks with their example gallery", async () => {
  const [app, readinessPhoto] = await Promise.all([
    source("App.jsx"),
    source("features/task-content/ReadinessPhotoTask.jsx"),
  ]);

  for (const criterion of ["camera_angle", "lighting", "background", "dressing"]) {
    assert.match(readinessPhoto, new RegExp(`\\["${criterion}"`));
  }
  assert.match(readinessPhoto, /<ReadinessExampleGallery initialActiveId=\{exampleFocusId\} \/>/);
  assert.doesNotMatch(readinessPhoto, /Teaching headset worn|佩戴授课耳麦|seven items|7 项/);
  assert.match(app, /four lesson-preparation checks/);
  assert.doesNotMatch(app, /all seven readiness checks|7 项准备检测结果/);
});

test("all four task summary regions use Shiwen content instead of local execution steps", async () => {
  const [app, localization, i18n] = await Promise.all([
    source("App.jsx"),
    source("task-localization.js"),
    source("i18n.jsx"),
  ]);

  assert.match(app, /<div>\{task\.reason\}<\/div>/);
  assert.match(app, /<div>\{task\.result\}<\/div>/);
  assert.match(app, /<div>\{task\.standard\}<\/div>/);
  assert.match(app, /<div>\{task\.value\}<\/div>/);
  assert.doesNotMatch(app, /task\.steps\.map/);
  assert.doesNotMatch(app, /!isProfileCredentials && !isLessonPreparation/);
  assert.match(localization, /const shiwenContent = \{/);
  assert.match(localization, /\.\.\.shiwenContent/);
  assert.match(i18n, /const shiwenContent = \{/);
  assert.match(i18n, /\.\.\.shiwenContent/);
});

test("personalized task details show only Shiwen why copy without extra evidence blocks", async () => {
  const [app, styles] = await Promise.all([
    source("App.jsx"),
    source("enhancements.css"),
  ]);

  assert.match(app, /function TaskEvidenceDetails/);
  assert.match(app, /facts=\{raw\.signalFacts\}/);
  assert.match(app, /raw\.taskCategory !== "personalized" && \(\s*<TaskEvidenceDetails/);
  assert.match(app, /"Related classes", "关联课程"/);
  assert.match(app, /course\.lessonId/);
  assert.match(styles, /\.task-related-courses li/);
});

test("the task runtime no longer contains local exam views", async () => {
  const taskFlow = await source("components/TaskFlow.jsx");
  const videoQuiz = await source("features/task-content/VideoLearningTask.jsx");
  const profileQuiz = await source("features/task-content/ProfileCredentialsTask.jsx");
  const integratedTaskFlow = await source("components/IntegratedTaskFlow.jsx");

  for (const runtime of [taskFlow, videoQuiz, profileQuiz, integratedTaskFlow]) {
    assert.doesNotMatch(runtime, /QUIZ|quizQuestions|passScore|Correct answer:|Submit answers/);
  }
});

test("teacher-facing task copy hides internal codes and prototype labels", async () => {
  const profileQuiz = await source("features/task-content/ProfileCredentialsTask.jsx");
  const videoQuiz = await source("features/task-content/VideoLearningTask.jsx");
  const taskFlow = await source("components/TaskFlow.jsx");
  const messages = await source("components/MessageCenter.jsx");
  const i18n = await source("i18n.jsx");

  assert.doesNotMatch(profileQuiz, /G01|来源系统|本系统|Mock/);
  assert.doesNotMatch(videoQuiz, /MOCK VIDEO|Mock ·|Mock learning/);
  assert.doesNotMatch(taskFlow, /MOCK CAMERA|站内课程 · MOCK|Take Mock photo|Mock 学习视频/);
  assert.doesNotMatch(messages, /MOCK DATA|>MOCK</);
  assert.doesNotMatch(i18n, /G01 的五项|来源系统|Mock 视频|Mock 数据|Mock 内容|Mock 练习|教师端 Mock/);
});
