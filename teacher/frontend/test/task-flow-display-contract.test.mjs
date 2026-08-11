import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = (path) => readFile(new URL(`../src/${path}`, import.meta.url), "utf8");

test("G01 keeps only the TESOL external status and embeds its Kuozhi assessment", async () => {
  const [profileTask, app] = await Promise.all([
    source("features/task-content/ProfileCredentialsTask.jsx"),
    source("App.jsx"),
  ]);

  assert.match(profileTask, /<ExternalStatusTask task=\{sourceStatusTask\} embedded \/>/);
  assert.match(profileTask, /<KuozhiCourseTask task=\{task\}/);
  assert.match(profileTask, /FOUR COMPLETION CONDITIONS/);
  assert.match(app, /Complete all four conditions in one place\./);
  assert.doesNotMatch(profileTask, /Self-intro|selfIntroComplete/);
  assert.doesNotMatch(app, /preview-self-intro|type: "self_intro"/);
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

test("G04 presents two independent vertical parts and completes only at 2/2", async () => {
  const [integratedTaskFlow, readinessPhoto, styles] = await Promise.all([
    source("components/IntegratedTaskFlow.jsx"),
    source("features/task-content/ReadinessPhotoTask.jsx"),
    source("features/task-content/readiness-photo-task.css"),
  ]);

  assert.doesNotMatch(readinessPhoto, /DeviceCheckTask|DEVICE_CHECK|data-g04-part="device"/);
  assert.match(readinessPhoto, /completedPartCount/);
  assert.match(readinessPhoto, /<small>\/2<\/small>/);
  assert.match(readinessPhoto, /role="progressbar"/);
  assert.match(readinessPhoto, /aria-label=\{c\("Completed G04 parts", "G04 已完成模块"\)\}/);
  assert.match(readinessPhoto, /aria-valuemax="2"/);
  assert.match(readinessPhoto, /aria-valuetext=/);
  assert.match(readinessPhoto, /data-g04-part="photo">[\s\S]*?<span className="g04-part-number">01<\/span>/);
  assert.match(readinessPhoto, /data-g04-part="courseware">[\s\S]*?<span className="g04-part-number">02<\/span>/);
  assert.match(readinessPhoto, /c\("Photo AI check", "照片 AI 检测"\)/);
  assert.match(readinessPhoto, /c\("Courseware preparation", "课件准备"\)/);
  assert.match(readinessPhoto, /disabled=\{opening\} onClick=\{openCamera\}/);
  assert.match(
    readinessPhoto,
    /disabled=\{!photoFile \|\| analyzing \|\| photoApproved\} onClick=\{submit\}/,
  );
  assert.doesNotMatch(readinessPhoto, /analyzing \|\| !coursewareConfirmed/);
  assert.match(readinessPhoto, /!nextPhotoApproved[\s\S]*!nextCoursewareConfirmed/);
  assert.doesNotMatch(readinessPhoto, /nextDevicePassed/);
  assert.match(integratedTaskFlow, /loadValidation: \(signal\) => getTaskValidation/);
  assert.match(readinessPhoto, /readinessPayloadFromValidation/);

  const deviceIndex = readinessPhoto.indexOf('data-g04-part="device"');
  const photoIndex = readinessPhoto.indexOf('data-g04-part="photo"');
  const coursewareIndex = readinessPhoto.indexOf('data-g04-part="courseware"');
  const captureIndex = readinessPhoto.indexOf('className="readiness-capture"');
  const submitIndex = readinessPhoto.indexOf('className="primary-button wide-button"');
  const privacyIndex = readinessPhoto.indexOf("readiness-privacy");
  const confirmationIndex = readinessPhoto.indexOf("readiness-preparation-confirm");
  assert.equal(deviceIndex, -1);
  assert.ok(photoIndex >= 0);
  assert.ok(coursewareIndex > photoIndex);
  assert.ok(captureIndex >= 0);
  assert.ok(submitIndex > captureIndex);
  assert.ok(privacyIndex > submitIndex);
  assert.ok(confirmationIndex > coursewareIndex);
  assert.match(styles, /\.g04-part-stack[\s\S]*grid-template-columns: minmax\(0, 1fr\)/);
  assert.doesNotMatch(styles, /\.g04-part-stack\s*\{[^}]*repeat\(3/);
});

test("G04 preserves an early-version completion without inventing current part results", async () => {
  const readinessPhoto = await source("features/task-content/ReadinessPhotoTask.jsx");

  assert.match(readinessPhoto, /const hasCurrentTwoPartCompletion = Boolean\(/);
  assert.match(readinessPhoto, /taskCompleted[\s\S]*photoProgress\?\.status === "COMPLETED"[\s\S]*coursewareProgress\?\.status === "COMPLETED"/);
  assert.match(readinessPhoto, /const grandfatheredTaskCompleted = taskCompleted && !hasCurrentTwoPartCompletion/);
  assert.match(readinessPhoto, /coursewareProgress\?\.details\?\.checklistVersion === coursewareStep\.config\.version/);
  assert.match(readinessPhoto, /!grandfatheredTaskCompleted && \([\s\S]*role="progressbar"/);
  assert.match(readinessPhoto, /c\("Your completed status is preserved", "已完成状态继续保留"\)/);
  assert.match(readinessPhoto, /grandfatheredTaskCompleted \? c\("Earlier version", "早期版本"\)/);
  assert.match(readinessPhoto, /grandfatheredTaskCompleted \? \([\s\S]*不显示“已通过”/);
  assert.match(readinessPhoto, /grandfatheredTaskCompleted \? \([\s\S]*无需补做确认/);
  assert.doesNotMatch(readinessPhoto, /DeviceCheckTask|deviceProgress|devicePassed/);
  assert.doesNotMatch(readinessPhoto, /taskCompleted \|\| coursewareProgress\?\.status === "COMPLETED"/);
});

test("G04 keeps partial photo and finalization failures retryable without rolling back passed parts", async () => {
  const [integratedTaskFlow, readinessPhoto] = await Promise.all([
    source("components/IntegratedTaskFlow.jsx"),
    source("features/task-content/ReadinessPhotoTask.jsx"),
  ]);

  assert.match(readinessPhoto, /const validation = persistedValidation \?\? response\?\.validation/);
  assert.match(readinessPhoto, /analysis\?\.teacherMessage/);
  assert.match(readinessPhoto, /validation\?\.resultCode === "STEPS_INCOMPLETE"/);
  assert.match(readinessPhoto, /completion\?\.status !== "COMPLETED"/);
  assert.match(readinessPhoto, /coursewareConfirmed && response\?\.status !== "COMPLETED"/);
  assert.match(readinessPhoto, /completedPartCount === 2 && !taskCompleted/);
  assert.match(readinessPhoto, /c\("Retry completion", "重试完成提交"\)/);
  assert.match(readinessPhoto, /void finalizeIfReady\(\{ reportTo: setCompletionError \}\)/);
  assert.doesNotMatch(readinessPhoto, /nextCoursewareConfirmed: true,[\s\S]{0,100}reportTo: setCoursewareError/);
  assert.match(integratedTaskFlow, /\["readiness_photo", "personalized_environment_photo"\]\.includes\(presentationTask\.method\)/);
  assert.doesNotMatch(
    readinessPhoto,
    /DeviceCheckTask|DEVICE_CHECK|setDevicePassed\(false\)|setCoursewareConfirmed\(false\)/,
  );
});

test("G04 uses Sophia's checklist first qualified photo as its camera reference", async () => {
  const [readinessPhoto, sharedPhotoPolicy] = await Promise.all([
    source("features/task-content/ReadinessPhotoTask.jsx"),
    source("features/task-content/teaching-environment-photo.js"),
  ]);

  assert.match(
    sharedPhotoPolicy,
    /lesson-preparation-examples\/camera-angle-good-front\.jpg/,
  );
  assert.match(readinessPhoto, /TEACHING_ENVIRONMENT_REFERENCE_PHOTO/);
  assert.doesNotMatch(readinessPhoto, /self-intro-reference-51talk\.webp/);
});

test("G04 shows Sophia's four visual checks with their example gallery", async () => {
  const [app, readinessPhoto, sharedPhotoPolicy, i18n] = await Promise.all([
    source("App.jsx"),
    source("features/task-content/ReadinessPhotoTask.jsx"),
    source("features/task-content/teaching-environment-photo.js"),
    source("i18n.jsx"),
  ]);

  for (const criterion of ["camera_angle", "lighting", "background", "dressing"]) {
    assert.match(sharedPhotoPolicy, new RegExp(`\\["${criterion}"`));
  }
  assert.match(readinessPhoto, /<ReadinessExampleGallery initialActiveId=\{exampleFocusId\} \/>/);
  assert.doesNotMatch(readinessPhoto, /Teaching headset worn|佩戴授课耳麦|seven items|7 项/);
  assert.match(app, /four AI checks/);
  assert.doesNotMatch(app, /all seven readiness checks|7 项准备检测结果/);
  assert.match(i18n, /两个部分可任意顺序操作/);
  assert.match(i18n, /授课画面照片的四项 AI 标准/);
  assert.doesNotMatch(i18n, /授课环境照片的七项检测|通过七项画面检测/);
});

test("personalized environment feedback uses the same live four-item photo check without G04 sections", async () => {
  const [taskFlow, personalizedPhoto, sharedPhotoPolicy] = await Promise.all([
    source("components/TaskFlow.jsx"),
    source("features/task-content/PersonalizedEnvironmentPhotoTask.jsx"),
    source("features/task-content/teaching-environment-photo.js"),
  ]);

  assert.match(taskFlow, /task\.method === "personalized_environment_photo"/);
  assert.match(taskFlow, /<PersonalizedEnvironmentPhotoTask task=\{task\} \/>/);
  assert.match(personalizedPhoto, /navigator\.mediaDevices\?\.getUserMedia/);
  assert.match(personalizedPhoto, /aspectRatio: \{ ideal: 16 \/ 9 \}/);
  assert.match(personalizedPhoto, /task\.execution\.uploadStep\(photoStep\.stepKey, photoFile\)/);
  assert.match(personalizedPhoto, /const response = await task\.execution\.submit\(\)/);
  assert.match(personalizedPhoto, /const reviewRequestRef = useRef\(0\)/);
  assert.match(personalizedPhoto, /const clearPhoto = \(\) => \{\s+if \(analyzing\) return;\s+reviewRequestRef\.current \+= 1;/);
  assert.match(personalizedPhoto, /const openCamera = async \(\) => \{\s+if \(analyzing\) return;/);
  assert.match(personalizedPhoto, /const capture = async \(\) => \{\s+if \(analyzing\) return;/);
  assert.match(personalizedPhoto, /if \(requestId !== reviewRequestRef\.current\) return;\s+const analysis = applyValidation/);
  assert.match(personalizedPhoto, /if \(passed\) \{\s+stopCamera\(\);\s+setPhotoApproved\(true\);/);
  assert.match(personalizedPhoto, /disabled=\{!cameraReady \|\| analyzing\} onClick=\{capture\}/);
  assert.match(personalizedPhoto, /disabled=\{analyzing\} onClick=\{clearPhoto\}/);
  assert.match(personalizedPhoto, /disabled=\{opening \|\| analyzing\} onClick=\{openCamera\}/);
  assert.match(personalizedPhoto, /readinessPayloadFromValidation/);
  assert.match(personalizedPhoto, /<ReadinessExampleGallery initialActiveId=\{exampleFocusId\} \/>/);
  assert.match(personalizedPhoto, /TEACHING_ENVIRONMENT_STANDARDS/);
  assert.match(personalizedPhoto, /useState\(taskCompleted\)/);
  assert.doesNotMatch(personalizedPhoto, /photoProgress\?\.status === "COMPLETED"/);
  assert.doesNotMatch(personalizedPhoto, /DeviceCheckTask|COURSEWARE_CONFIRMATION|completedPartCount/);
  for (const criterion of ["camera_angle", "lighting", "background", "dressing"]) {
    assert.match(sharedPhotoPolicy, new RegExp(`\\["${criterion}"`));
  }
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
