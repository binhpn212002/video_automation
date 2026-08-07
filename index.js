// check-bunny.js

const BUNNY_REGION = "FS";

const BUNNY_STORAGE_ZONE = "quanlyvmaster";
const BUNNY_STORAGE_ZONE_API_KEY = "6253b93a-030c-4559-a84257bcdd97-a7c0-405e";

const BUNNY_STREAM_LIBRARY_ID = "368067";
const BUNNY_STREAM_LIBRARY_API_KEY = "e3418da4-a742-44e4-bc2c-106c9af56162";

const BUNNY_MANAGE_FILE_API_URL = "https://storage.bunnycdn.com";
const BUNNY_VIDEO_LIB_URL = "https://video.bunnycdn.com/library";


// ===============================
// List Bunny Storage
// ===============================

async function listStorage(path = "") {

    const url =
        `${BUNNY_MANAGE_FILE_API_URL}/${BUNNY_STORAGE_ZONE}/${path}`;


    const res = await fetch(url, {
        headers: {
            "AccessKey": BUNNY_STORAGE_ZONE_API_KEY
        }
    });


    if (!res.ok) {
        console.log("Storage error:", await res.text());
        return;
    }


    const data = await res.json();


    console.log("\n===== BUNNY STORAGE =====");


    let total = 0;


    for (const item of data) {

        if (item.IsDirectory) {

            console.log(
                "📁",
                item.ObjectName
            );

        } else {

            total += item.Length;


            console.log(
                "📄",
                item.ObjectName,
                `${(item.Length / 1024 / 1024).toFixed(2)} MB`
            );
        }
    }


    console.log(
        "TOTAL:",
        (total / 1024 / 1024 / 1024).toFixed(3),
        "GB"
    );
}



// ===============================
// List Bunny Stream Videos
// ===============================

async function listVideos() {


    const url =
        `${BUNNY_VIDEO_LIB_URL}/${BUNNY_STREAM_LIBRARY_ID}/videos?page=1&itemsPerPage=100`;


    const res = await fetch(url, {

        headers: {
            "AccessKey": BUNNY_STREAM_LIBRARY_API_KEY
        }

    });


    if (!res.ok) {

        console.log(
            "Stream error:",
            await res.text()
        );

        return;
    }


    const data = await res.json();


    console.log("\n===== BUNNY STREAM =====");


    for (const video of data.items || []) {

        console.log(
            "🎬",
            video.title,
            "| ID:",
            video.guid,
            "| Length:",
            video.length,
            "sec",
            "| Size:",
            video.storageSize
                ? (video.storageSize / 1024 / 1024).toFixed(2) + " MB"
                : "unknown"
        );
    }
}



// ===============================
// RUN
// ===============================

(async()=>{

    await listStorage();

    await listVideos();

})();