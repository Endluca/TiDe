import { publicAsset } from "../../public-assets";

export const TEACHING_ENVIRONMENT_REFERENCE_PHOTO = publicAsset(
  "/readiness/lesson-preparation-examples/camera-angle-good-front.jpg",
);

export const TEACHING_ENVIRONMENT_STANDARDS = {
  en: [
    ["camera_angle", "Camera angle", "Show one teacher with a clear, unobstructed face and chest-up upper body. Keep your head centered, leave a little space above it, and position the camera near eye level."],
    ["lighting", "Lighting", "Ensure both sides of your face and all facial features are clearly visible in even, front-facing light. Avoid darkness, heavy shadows, overexposure, strong backlighting, or masking filters."],
    ["background", "Background", "Use a clean, stable background free of unrelated people, animals, clutter, and identifiable private information. Virtual backgrounds must not cover or distort you."],
    ["dressing", "Dressing", "Wear a neat, professional top with your shoulders and neckline clearly visible. Avoid sleepwear, loungewear, sleeveless or overly casual clothing, and distracting accessories."],
  ],
  zh: [
    ["camera_angle", "摄像头角度", "必须正脸面对摄像头并保持头部端正，脸部和肩部尽量贴合辅助线，摄像头与视线平齐；侧脸、明显转头、仰头、低头或头部侧倾不通过。"],
    ["lighting", "光线", "面部光线均匀、明亮，不能过暗、过曝或有明显逆光。"],
    ["background", "背景", "背景干净、合适且不分散注意力；使用虚拟背景时须显示清晰，不遮挡面部或身体。"],
    ["dressing", "着装", "穿着整洁、专业，并适合给少儿进行线上授课。"],
  ],
};

export const teachingEnvironmentCameraErrors = (c) => ({
  NotAllowedError: c(
    "Camera permission was not granted. Allow camera access in the browser site settings, then try again.",
    "未获得摄像头权限。请在浏览器地址栏的站点设置中允许使用摄像头后重试。",
  ),
  NotFoundError: c(
    "No camera was found. Check that a camera is available, then try again.",
    "没有检测到可用摄像头，请确认设备摄像头可用后重试。",
  ),
  NotReadableError: c(
    "The camera is being used by another app. Close that app, then try again.",
    "摄像头正被其他程序占用，请关闭占用程序后重试。",
  ),
});
