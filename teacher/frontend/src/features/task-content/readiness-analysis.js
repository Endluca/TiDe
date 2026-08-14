const PENDING_BACKEND_STATUSES = new Set(["UPLOADING", "CHECKING", "BEAUTIFYING"]);
const UNAVAILABLE_BACKEND_STATUSES = new Set(["UNDER_REVIEW", "PROCESSING_FAILED"]);
export const READINESS_MIN_CONFIDENCE = 0.85;
export const READINESS_CENTRAL_MIN_MEAN_LUMA = 60;
export const READINESS_CENTRAL_MAX_CLIPPED_RATIO = 0.2;

const CHECK_STATUS_COPY = {
  camera_angle: {
    pass: ["The camera angle meets the current requirement.", "摄像头角度符合当前要求。"],
    fail: ["The camera angle needs adjustment.", "摄像头角度需要调整。"],
    uncertain: ["The camera angle could not be assessed reliably.", "暂时无法可靠判断摄像头角度。"],
  },
  lighting: {
    pass: ["The lighting meets the current requirement.", "光线符合当前要求。"],
    fail: ["The lighting needs adjustment.", "光线需要调整。"],
    uncertain: ["The lighting could not be assessed reliably.", "暂时无法可靠判断光线。"],
  },
  background: {
    pass: ["The background meets the current requirement.", "背景符合当前要求。"],
    fail: ["The background needs adjustment.", "背景需要调整。"],
    uncertain: ["The background could not be assessed reliably.", "暂时无法可靠判断背景。"],
  },
  dressing: {
    pass: ["The visible clothing meets the current requirement.", "画面中的着装符合当前要求。"],
    fail: ["The visible clothing needs adjustment.", "画面中的着装需要调整。"],
    uncertain: ["The visible clothing could not be assessed reliably.", "暂时无法可靠判断画面中的着装。"],
  },
};

export function isReadinessPendingStatus(status) {
  return PENDING_BACKEND_STATUSES.has(String(status || "").toUpperCase());
}

function isChineseCopy(c) {
  return c("__en__", "__zh__") === "__zh__";
}

function checkStatusMessage(checkId, status, c) {
  const copy = CHECK_STATUS_COPY[checkId]?.[status];
  if (copy) return c(copy[0], copy[1]);
  if (status === "pass") {
    return c("Meets the current requirement.", "符合当前要求。");
  }
  if (status === "fail") {
    return c(
      "This item needs adjustment before you retake the photo.",
      "本项需要调整后重新拍照。",
    );
  }
  return c(
    "This item could not be assessed reliably.",
    "本项暂时无法可靠判断。",
  );
}

function stableTeacherMessage(status, backendStatus, backendDecision, c) {
  if (status === "approved") {
    return c(
      "All visible items meet the current requirement.",
      "画面中的各项内容均符合当前要求。",
    );
  }
  if (status === "changes_requested") {
    return c(
      "Some items need adjustment. Review the results, then retake the photo.",
      "部分项目需要调整，请查看检测结果后重新拍照。",
    );
  }
  if (status === "processing") {
    if (backendStatus === "UPLOADING") {
      return c(
        "The photo is being uploaded.",
        "照片正在上传中。",
      );
    }
    if (backendStatus === "BEAUTIFYING") {
      return c(
        "The check passed. The photo is being prepared and saved.",
        "画面检测已通过，正在处理并保存照片。",
      );
    }
    return c(
      "The camera view is being checked. This usually takes 10-15 seconds.",
      "正在审核，预计 10-15 秒。",
    );
  }
  if (backendStatus === "UNDER_REVIEW") {
    return c(
      "The automatic camera check is taking longer than expected. Please try again later.",
      "自动画面检测耗时较长，请稍后重新检测。",
    );
  }
  if (
    backendStatus === "PROCESSING_FAILED" ||
    backendDecision === "ERROR"
  ) {
    return c(
      "The automatic camera check could not be completed. Please try again.",
      "自动画面检测未能完成，请重新检测。",
    );
  }
  return c(
    "The automatic camera check is temporarily unavailable. Please try again.",
    "自动画面检测暂时不可用，请重新检测。",
  );
}

export function centralExposureFailure(signals) {
  if (!signals) return null;
  if (signals.centralMeanLuma < READINESS_CENTRAL_MIN_MEAN_LUMA) return "TOO_DARK";
  if (signals.centralClippedLumaRatio > READINESS_CENTRAL_MAX_CLIPPED_RATIO) return "OVEREXPOSED";
  return null;
}

export async function analyzePhotoCentralExposure(file) {
  const image = await createImageBitmap(file);
  try {
    const canvas = document.createElement("canvas");
    canvas.width = image.width;
    canvas.height = image.height;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    context.drawImage(image, 0, 0);
    const left = Math.floor(image.width * 0.4);
    const top = Math.floor(image.height * 0.2);
    const width = Math.max(1, Math.ceil(image.width * 0.2));
    const height = Math.max(1, Math.ceil(image.height * 0.35));
    const pixels = context.getImageData(left, top, width, height).data;
    let pixelCount = 0;
    let lumaTotal = 0;
    let clippedCount = 0;
    for (let offset = 0; offset < pixels.length; offset += 4) {
      const luma = 0.2126 * pixels[offset]
        + 0.7152 * pixels[offset + 1]
        + 0.0722 * pixels[offset + 2];
      pixelCount += 1;
      lumaTotal += luma;
      if (luma >= 245) clippedCount += 1;
    }
    return {
      centralMeanLuma: lumaTotal / pixelCount,
      centralClippedLumaRatio: clippedCount / pixelCount,
    };
  } finally {
    image.close();
  }
}

export function readinessPayloadFromValidation(validation) {
  const review = validation?.imageReview;
  if (!review || !['PASS', 'RETRY', 'ERROR'].includes(review.decision)) {
    return null;
  }
  const confidenceByCriterion = review.confidenceSummary?.criteria || {};
  return {
    status:
      review.decision === 'PASS'
        ? 'READY'
        : review.decision === 'RETRY'
          ? 'RETRY_REQUIRED'
          : 'UNDER_REVIEW',
    decision: review.decision,
    teacherMessage: review.teacherReason,
    confidenceSummary: review.confidenceSummary,
    checks: (review.items || []).map((item) => ({
      id: item.criterionKey,
      status:
        item.result === 'PASS'
          ? 'pass'
          : item.result === 'FAIL'
            ? 'fail'
            : 'uncertain',
      confidence: confidenceByCriterion[item.criterionKey],
      message: item.teacherMessage,
    })),
  };
}

export function normalizeReadinessAnalysis(payload, criteria, c, exposureSignals = null) {
  const backendStatus = String(payload?.status || "").toUpperCase();
  const backendDecision = String(payload?.decision || "").toUpperCase();
  const useBackendCopy = isChineseCopy(c);
  const suppliedChecks = new Map(
    (Array.isArray(payload?.checks) ? payload.checks : []).map((check) => [
      check.id || check.code,
      check,
    ]),
  );
  let checks = criteria.map((standard) => {
    const supplied = suppliedChecks.get(standard.id) || {};
    const rawStatus = String(supplied.status || "").toLowerCase();
    const suppliedConfidence = Number.isFinite(supplied.confidence)
      ? supplied.confidence
      : payload?.confidenceSummary?.criteria?.[standard.id];
    const confidence = Number.isFinite(suppliedConfidence)
      ? suppliedConfidence
      : null;
    const validatedStatus = ["pass", "fail", "uncertain"].includes(rawStatus)
      ? rawStatus
      : "uncertain";
    const status = validatedStatus === "pass"
      && confidence !== null
      && confidence < READINESS_MIN_CONFIDENCE
      ? "uncertain"
      : validatedStatus;
    return {
      id: standard.id,
      title: standard.title,
      status,
      confidence,
      message:
        (useBackendCopy && supplied.message) ||
        checkStatusMessage(standard.id, status, c),
      suggestion:
        status === "pass"
          ? ""
          : (useBackendCopy && supplied.suggestion) || standard.detail,
    };
  });
  const exposureIssue = centralExposureFailure(exposureSignals);
  const cameraPassed = checks.find((check) => check.id === "camera_angle")?.status === "pass";
  if (cameraPassed && exposureIssue) {
    checks = checks.map((check) => check.id !== "lighting" || check.status !== "pass"
      ? check
      : {
          ...check,
          status: "fail",
          message: exposureIssue === "OVEREXPOSED"
            ? c("The center of the image is clearly overexposed or affected by strong glare.", "画面中央区域存在明显过曝或强眩光。")
            : c("The center of the image is too dark.", "画面中央区域明显过暗。"),
          suggestion: exposureIssue === "OVEREXPOSED"
            ? c("Lower the front light or change its angle so facial details remain visible.", "降低正面光源亮度或调整角度，保留清楚的面部细节。")
            : c("Add even front light so both sides of the face are visible.", "增加均匀的正面光线，让面部两侧和五官清楚可见。"),
        });
  }
  const hasCompleteChecks = criteria.every((standard) => {
    const check = suppliedChecks.get(standard.id);
    return ["pass", "fail", "uncertain"].includes(
      String(check?.status || "").toLowerCase(),
    );
  });
  const allPassed = hasCompleteChecks && checks.every((check) => check.status === "pass");

  let status;
  if (isReadinessPendingStatus(backendStatus)) {
    status = "processing";
  } else if (
    UNAVAILABLE_BACKEND_STATUSES.has(backendStatus) ||
    backendDecision === "ERROR"
  ) {
    status = "unavailable";
  } else if (backendStatus === "READY") {
    status = allPassed ? "approved" : "unavailable";
  } else if (backendStatus === "RETRY_REQUIRED" || backendDecision === "RETRY") {
    status = hasCompleteChecks ? "changes_requested" : "unavailable";
  } else {
    status = allPassed
      ? "approved"
      : hasCompleteChecks
        ? "changes_requested"
        : "unavailable";
  }

  const visibleChecks =
    status === "approved" || status === "changes_requested" ? checks : [];

  return {
    status,
    provider: payload?.provider || "readiness-ai",
    modelVersion: payload?.modelVersion || null,
    checkedAt: payload?.checkedAt || new Date().toISOString(),
    checks: visibleChecks,
    confidenceSummary: payload?.confidenceSummary || null,
    exposureSignals,
    exposureIssue,
    teacherMessage:
      (useBackendCopy && payload?.teacherMessage) ||
      stableTeacherMessage(status, backendStatus, backendDecision, c),
  };
}
