from app.lin import UserGroup as LinUserGroup
from app.lin import db


class UserGroup(LinUserGroup):
    @classmethod
    def delete_batch_by_user_id_and_group_ids(cls, user_id, group_ids: list, commit=False):
        cls.query.filter_by(user_id=user_id).filter(cls.group_id.in_(group_ids)).delete(synchronize_session=False)
        if commit:
            db.session.commit()

    @classmethod
    def transfer_users_to_group(cls, from_group_id, to_group_id, user_ids: list, commit=False):
        """将 user_ids 中的用户从 from_group_id 安全迁移到 to_group_id

        - 已属于目标分组的用户跳过，避免产生重复关联；
        - 为其余用户新增到目标分组的关联记录；
        - 删除源分组的全部用户关联记录（连带清理已删除用户的残留行）。
        """
        if user_ids:
            # 已在目标分组中的用户，避免重复关联
            existing_user_ids = {
                relation.user_id
                for relation in cls.query.filter(cls.group_id == to_group_id, cls.user_id.in_(user_ids)).all()
            }
            new_relations = []
            for user_id in user_ids:
                if user_id in existing_user_ids:
                    continue
                relation = cls()
                relation.user_id = user_id
                relation.group_id = to_group_id
                new_relations.append(relation)
            if new_relations:
                db.session.add_all(new_relations)
        # 删除源分组下的全部用户关联记录
        cls.query.filter_by(group_id=from_group_id).delete(synchronize_session=False)
        if commit:
            db.session.commit()
