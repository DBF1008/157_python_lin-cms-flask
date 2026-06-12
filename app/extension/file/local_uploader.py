import os

from flask import current_app
from werkzeug.utils import secure_filename

from app.lin import Uploader, file_meta

from .file import File


class LocalUploader(Uploader):
    def upload(self):
        ret = []
        self.mkdir_if_not_exists()
        site_domain = current_app.config.get(
            "SITE_DOMAIN",
            "http://{host}:{port}".format(
                host=current_app.config.get("FLASK_RUN_HOST", "127.0.0.1"),
                port=current_app.config.get("FLASK_RUN_PORT", "5000"),
            ),
        )
        for single in self._file_storage:
            data = single.read()
            single.seek(0)
            file_md5 = self._generate_md5(data)
            size = len(data)
            exists = File.select_by_md5(file_md5)
            if exists:
                ret.append(
                    file_meta(
                        id=exists.id,
                        key=single.name,
                        url=site_domain + os.path.join(current_app.static_url_path, exists.path),
                        file_name=single.filename,
                        file_key=exists.path,
                        size=size,
                        path=exists.path,
                    )
                )
            else:
                absolute_path, relative_path, real_name = self._get_store_path(single.filename)
                secure_filename(single.filename)
                single.save(absolute_path)
                file = File.create_file(
                    name=real_name,
                    path=relative_path,
                    extension=self._get_ext(single.filename),
                    size=size,
                    md5=file_md5,
                    commit=True,
                )
                ret.append(
                    file_meta(
                        id=file.id,
                        key=single.name,
                        url=site_domain + os.path.join(current_app.static_url_path, file.path),
                        file_name=single.filename,
                        file_key=file.path,
                        size=size,
                        path=file.path,
                    )
                )
        return ret
