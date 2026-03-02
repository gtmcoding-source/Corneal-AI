document.querySelector("form")?.addEventListener("submit", function(){
    let loader = document.createElement("div");
    loader.innerHTML = "🤖 AI is thinking...";
    loader.style.position="fixed";
    loader.style.top="50%";
    loader.style.left="50%";
    loader.style.transform="translate(-50%,-50%)";
    loader.style.background="black";
    loader.style.padding="20px";
    loader.style.borderRadius="10px";
    document.body.appendChild(loader);
});