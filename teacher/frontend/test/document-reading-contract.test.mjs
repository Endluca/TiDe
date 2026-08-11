import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = (path) => readFile(new URL(`../src/${path}`, import.meta.url), "utf8");

test("G02 renders a safe native document and accepts atomic completion from save", async () => {
  const [
    component,
    taskFlow,
    adapter,
    backendContentService,
    backendController,
    backendDockerfile,
    markdown,
    markdownZh,
    progressHelper,
    integratedFlow,
    taskApi,
    styles,
    controlledImage,
    app,
    main,
  ] = await Promise.all([
    source("features/task-content/DocumentReadingTask.jsx"),
    source("components/TaskFlow.jsx"),
    source("task-adapter.js"),
    readFile(new URL("../../backend/src/tasks/task-document-content.service.ts", import.meta.url), "utf8"),
    readFile(new URL("../../backend/src/tasks/task.controller.ts", import.meta.url), "utf8"),
    readFile(new URL("../../backend/Dockerfile", import.meta.url), "utf8"),
    readFile(new URL("../../backend/content/g02/2026-07-24-overseas-nt-policies-v1/en.md", import.meta.url), "utf8"),
    readFile(new URL("../../backend/content/g02/2026-07-24-overseas-nt-policies-v1/zh.md", import.meta.url), "utf8"),
    source("features/task-content/document-reading-progress.js"),
    source("components/IntegratedTaskFlow.jsx"),
    source("api/task-api.js"),
    source("features/task-content/document-reading-task.css"),
    readFile(new URL(
      "../../backend/content/g02/2026-07-24-overseas-nt-policies-v1/updated-unlocking-process.jpg",
      import.meta.url,
    )),
    source("App.jsx"),
    source("main.jsx"),
  ]);

  assert.match(taskFlow, /task\.method === "document_reading"/);
  assert.match(taskFlow, /lazy\([\s\S]*import\("\.\.\/features\/task-content\/DocumentReadingTask"\)/);
  assert.doesNotMatch(taskFlow, /import DocumentReadingTask from/);
  assert.match(taskFlow, /<DocumentReadingTaskPanel task=\{task\} \/>/);
  assert.doesNotMatch(adapter, /new Set\(\["G02"/);
  assert.match(adapter, /step\.type === "DOCUMENT"\)\) return "document_reading"/);
  assert.match(component, /latest\.execution\.saveStep/);
  assert.match(component, /response\?\.step\?\.status === "COMPLETED"/);
  assert.match(component, /response\?\.status === "COMPLETED"/);
  assert.match(component, /assignmentFinishedRef\.current = true/);
  assert.match(component, /await finishAssignment\(\)/);
  assert.match(component, /await latest\.execution\.submit\(\{ keepalive: true \}\)/);
  assert.match(integratedFlow, /if \(response\?\.status === "COMPLETED"\)/);
  assert.match(integratedFlow, /await onTaskSubmitted\?\.\(response\)/);
  assert.match(integratedFlow, /const latest = await refreshLatestContext\(\)/);
  assert.match(integratedFlow, /latest\?\.status === "COMPLETED"/);
  assert.match(integratedFlow, /recoveredDetails\.reachedEnd === true/);
  assert.match(integratedFlow, /recoveredDetails\.contentHash === step\.config\?\.contentHash/);
  assert.doesNotMatch(component, /dangerouslySetInnerHTML|rehypeRaw/);
  assert.match(component, /skipHtml/);
  assert.match(component, /rel="noreferrer noopener"/);
  assert.match(component, /safePolicyDocumentUrl/);
  assert.match(component, /getTaskDocumentContent\(taskInstanceId, controller\.signal\)/);
  assert.match(component, /getTaskDocumentAsset\(/);
  assert.match(component, /URL\.createObjectURL\(blob\)/);
  assert.match(component, /URL\.revokeObjectURL\(objectUrl\)/);
  assert.match(taskApi, /responseType: "blob"/);
  assert.match(component, /language === "zh" \? loadedContent\?\.markdown\?\.zh/);
  assert.match(component, /loadedContent\.contentVersion === contentVersion/);
  assert.match(component, /loadedContent\.contentHash === contentHash/);
  assert.match(backendController, /document-content/);
  assert.match(backendContentService, /'content', 'g02'/);
  assert.match(backendDockerfile, /COPY --chown=node:node content \.\/content/);
  assert.match(component, /key=\{language\}[\s\S]*className="document-reading-scroll"/);
  assert.match(
    component,
    /\[assignmentCompleted, contentCompatible, documentCompleted, language, scheduleMeasure, taskIdentity\]/,
  );
  assert.match(component, /width="1917"/);
  assert.match(component, /height="1073"/);
  assert.match(component, /loading="eager"/);
  assert.match(styles, /\.document-reading-markdown img[\s\S]*width: 100%[\s\S]*height: auto/);
  assert.doesNotMatch(component, /sourceNodeId|sourceUrl|dingtalk.*entry/iu);

  assert.match(component, /pendingProgressRef/);
  assert.match(component, /persistPromiseRef/);
  assert.match(component, /submitPromiseRef/);
  assert.match(component, /pagehide/);
  assert.match(component, /visibilitychange/);
  assert.match(component, /ResizeObserver/);
  assert.match(progressHelper, /INVALID_DOCUMENT_PROGRESS_RESPONSE/);
  assert.ok(
    component.indexOf("const acknowledged = documentReadProgressFromStep")
      < component.indexOf("confirmedProgressRef.current = mergeDocumentReadProgress"),
    "the save response must be validated before confirmed progress advances",
  );
  assert.match(integratedFlow, /requestOptions = \{\}/);
  assert.match(taskApi, /keepalive: requestOptions\.keepalive === true/);

  assert.match(component, /historicalCompletion = assignmentCompleted && !documentCompleted/);
  assert.match(component, /contentCompatible \? progress\.readPercent : 0/);
  assert.match(component, /This task was completed previously/);
  assert.doesNotMatch(component, /completed \? 100 : progress\.readPercent/);

  assert.match(taskFlow, /isDocumentReadOnlyPreview && <DocumentReadingTaskPanel task=\{previewTask\} \/>/);
  assert.match(taskFlow, /isDocumentReadOnlyPreview = isDocument && readOnlyPreview/);
  assert.match(taskFlow, /execution: \{ live: false \}/);
  assert.doesNotMatch(taskFlow, /G02_POLICY_DOCUMENT|contentVersion:\s*"2026-/);
  assert.match(app, /const policyDocumentTask = catalogTask\.taskCode === "G02"/);
  assert.match(app, /"About 35 min", "约 35 分钟"/);
  assert.match(app, /"G02 is completed automatically after you reach the end of the current published document\."/);
  assert.match(main, /"\/preview\/g02"/);
  assert.match(main, /"\/task\/platform-policies"/);
  assert.match(main, /onboardingGuideInitiallyOpen=\{previewRoute !== "\/preview\/g02"\}/);
  assert.match(component, /readOnly: task\.previewReadOnly === true/);
  assert.match(component, /if \(!canPersistDocumentProgress\(\{ readOnly: latest\.readOnly \}\)\) return/);
  assert.doesNotMatch(component, /G02_POLICY_DOCUMENT/);
  assert.match(component, /server returns the exact document version/);
  assert.match(component, /\{contentCompatible \? \(/);
  assert.match(progressHelper, /contentCompatible && !readOnly/);

  const hash = createHash("sha256").update(markdown).digest("hex");
  assert.match(backendContentService, new RegExp(hash));
  const translatedHash = createHash("sha256").update(markdownZh).digest("hex");
  assert.match(backendContentService, new RegExp(translatedHash));
  assert.match(markdownZh, /[\u4e00-\u9fff]/u);
  const sourceUrls = [...markdown.matchAll(/https?:\/\/[^\s)\]]+/gu)].map(([url]) => url).sort();
  const translatedUrls = [...markdownZh.matchAll(/https?:\/\/[^\s)\]]+/gu)].map(([url]) => url).sort();
  assert.deepEqual(translatedUrls, sourceUrls, "the Chinese document must preserve every source URL");
  const headingLevels = (value) => [...value.matchAll(/^(#{1,6})\s/gmu)].map(([, marks]) => marks.length);
  assert.deepEqual(
    headingLevels(markdownZh),
    headingLevels(markdown),
    "the Chinese document must preserve the source heading hierarchy",
  );
  const imageSources = (value) => [...value.matchAll(/!\[[^\]]*\]\(([^ )]+)/gu)]
    .map(([, sourcePath]) => sourcePath);
  assert.deepEqual(
    imageSources(markdownZh),
    imageSources(markdown),
    "the Chinese document must preserve every source image reference",
  );
  const sourceImagePath = markdown.match(/!\[[^\]]*\]\((\/core\/api\/resources\/img\/[^ )]+)/u)?.[1];
  assert.ok(sourceImagePath, "the source Markdown image reference must remain traceable");
  assert.ok(backendContentService.includes(sourceImagePath), "the source image must map to the controlled backend asset");
  const controlledImageHash = createHash("sha256").update(controlledImage).digest("hex");
  assert.equal(
    controlledImageHash,
    "960dfdde25ba3b4b7362694714cd498f069482bfa4b09a97df8f3cc06a829fc9",
  );
  assert.match(backendContentService, new RegExp(controlledImageHash));
});
