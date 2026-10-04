// My Documents: drag & drop onto the upload area, and upload as soon as a file is chosen.
// Without JavaScript the form still works with the Browse and Upload buttons.

(() => {
  const zone = document.getElementById("dropzone");
  if (!zone) return;
  const input = document.getElementById("file");

  zone.classList.add("js");

  function upload() {
    if (!input.files.length) return;
    zone.classList.add("busy");
    zone.submit();
  }

  input.addEventListener("change", upload);

  ["dragenter", "dragover"].forEach((type) =>
    zone.addEventListener(type, (e) => {
      e.preventDefault();
      zone.classList.add("over");
    })
  );
  ["dragleave", "drop"].forEach((type) =>
    zone.addEventListener(type, (e) => {
      e.preventDefault();
      zone.classList.remove("over");
    })
  );
  zone.addEventListener("drop", (e) => {
    if (!e.dataTransfer.files.length) return;
    input.files = e.dataTransfer.files;
    upload();
  });
})();
